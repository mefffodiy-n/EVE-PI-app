"""
scripts/esi_client: единственная точка обращений к ESI.

Проверяется то, что прямо требует документация CCP (CLAUDE.md, правило 11) —
лимит ошибок, 420/429 с Retry-After, и главное для этого файла: ETag и
Expires. До 12.09.2026 клиент честно слал запрос всегда и получал 304
постфактум — сама документация называет это обходом кэша, если запрос
уходит раньше Expires. Тесты бьют по этому: с сохранённым свежим Expires
второй вызов не должен трогать сеть вообще.
"""

from __future__ import annotations

import json
from email.utils import format_datetime

import pytest

from scripts.esi_client import EsiClient


@pytest.fixture(autouse=True)
def _etag_store(monkeypatch, tmp_path):
    import scripts.esi_client as ec

    monkeypatch.setattr(ec, "ETAG_STORE", tmp_path / "etags.json")


def _http_date(dt):
    return format_datetime(dt, usegmt=True)


def _counting_opener(calls, status=200, body="{}", headers=None):
    def opener(url, request_headers):
        calls.append(dict(request_headers))
        return status, body, (headers or {})
    return opener


class TestEtagAndExpires:
    def test_etag_sent_as_if_none_match_on_next_request(self):
        calls = []
        opener = _counting_opener(calls, headers={"ETag": '"abc"'})
        client = EsiClient(opener=opener)

        client.get("/status/")
        client.get("/status/")

        assert "If-None-Match" not in calls[0]
        assert calls[1]["If-None-Match"] == '"abc"'

    def test_request_skipped_entirely_while_expires_in_future(self):
        """
        Сама суть фикса: второй вызов не должен дойти до opener() —
        ESI требует не запрашивать раньше Expires, а не только слать
        If-None-Match и получать честный 304.
        """
        from datetime import datetime, timedelta, timezone

        calls = []
        future = _http_date(datetime.now(timezone.utc) + timedelta(minutes=5))
        opener = _counting_opener(calls, headers={"ETag": '"abc"', "Expires": future})
        client = EsiClient(opener=opener)

        first = client.get("/status/")
        second = client.get("/status/")

        assert len(calls) == 1, "второй запрос обязан быть подавлен локальным кэшем"
        assert first.status == 200
        assert second.status == 304
        assert second.from_cache is True

    def test_request_sent_again_once_expires_has_passed(self):
        from datetime import datetime, timedelta, timezone

        calls = []
        past = _http_date(datetime.now(timezone.utc) - timedelta(minutes=5))
        opener = _counting_opener(calls, headers={"ETag": '"abc"', "Expires": past})
        client = EsiClient(opener=opener)

        client.get("/status/")
        client.get("/status/")

        assert len(calls) == 2, "просроченный Expires не должен подавлять запрос"

    def test_use_etag_false_ignores_local_cache(self):
        """
        Свежий локальный кэш есть, но конкретный вызов явно попросил его
        не использовать (use_etag=False) — должен дойти до сети.
        """
        from datetime import datetime, timedelta, timezone

        calls = []
        future = _http_date(datetime.now(timezone.utc) + timedelta(minutes=5))
        opener = _counting_opener(calls, headers={"ETag": '"abc"', "Expires": future})
        client = EsiClient(opener=opener)

        client.get("/status/")
        client.get("/status/", use_etag=False)

        assert len(calls) == 2

    def test_missing_expires_header_falls_back_to_etag_only(self):
        """Без Expires — прежнее поведение: запрос уходит, сервер решает 200/304."""
        calls = []
        opener = _counting_opener(calls, headers={"ETag": '"abc"'})
        client = EsiClient(opener=opener)

        client.get("/status/")
        client.get("/status/")

        assert len(calls) == 2

    def test_old_etag_only_cache_file_still_loads(self):
        """
        Формат до 12.09.2026 — {url: "etag-строка"} без Expires. Новый код
        должен читать такой файл, не выбрасывая его и не падая.
        """
        import scripts.esi_client as ec

        ec.ETAG_STORE.parent.mkdir(parents=True, exist_ok=True)
        ec.ETAG_STORE.write_text(
            json.dumps({"https://esi.evetech.net/status/": '"old-etag"'}),
            encoding="utf-8",
        )

        calls = []
        opener = _counting_opener(calls, status=304, headers={})
        client = EsiClient(opener=opener)
        response = client.get("/status/")

        assert calls[0]["If-None-Match"] == '"old-etag"'
        assert response.status == 304

    def test_304_without_fresh_etag_extends_expires_for_known_etag(self):
        """
        Настоящий 304 с новым Expires (но без повторного ETag в
        заголовках) — локальный кэш всё равно продлевается для уже
        известного ETag, следующий вызов снова подавляется без сети.
        """
        from datetime import datetime, timedelta, timezone

        calls = []
        near_future = _http_date(datetime.now(timezone.utc) + timedelta(seconds=1))
        far_future = _http_date(datetime.now(timezone.utc) + timedelta(minutes=5))

        responses = iter([
            (200, "{}", {"ETag": '"abc"', "Expires": near_future}),
            (304, "", {"Expires": far_future}),
        ])

        def opener(url, headers):
            calls.append(dict(headers))
            return next(responses)

        client = EsiClient(opener=opener)
        client.get("/status/")

        import time
        time.sleep(1.1)

        second = client.get("/status/")
        third = client.get("/status/")

        assert second.status == 304 and second.from_cache is True
        assert len(calls) == 2, "третий вызов обязан быть подавлен продлённым Expires"
        assert third.from_cache is True

    def test_error_limit_and_retry_after_untouched_by_expires_change(self):
        """Смежная логика (правило 11) не должна была пострадать от рефакторинга."""
        from scripts.esi_client import EsiRateLimited

        def opener(url, headers):
            return 420, "{}", {"Retry-After": "30"}

        client = EsiClient(opener=opener)
        with pytest.raises(EsiRateLimited) as exc_info:
            client.get("/status/")
        assert exc_info.value.retry_after == 30
