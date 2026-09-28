"""
Тексты ошибок API переводимы (правило 9): код + параметры, язык — из запроса.

НАЙДЕНО 29.09.2026 (внешняя рецензия): ~50 вызовов json_error несли
захардкоженный русский текст, англоязычный интерфейс получал русские
ошибки.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from api.errors import _CATALOG, render_error
from domain.plan_storage import PlanStorageError

ROOT = Path(__file__).resolve().parent.parent


def test_catalog_has_both_languages():
    for code, entry in _CATALOG.items():
        assert set(entry) == {"ru", "en"}, code
        assert entry["ru"] and entry["en"], code


def test_catalog_params_match_between_languages():
    for code, entry in _CATALOG.items():
        ru = set(re.findall(r"{(\w+)}", entry["ru"]))
        en = set(re.findall(r"{(\w+)}", entry["en"]))
        assert ru == en, code


def test_json_error_calls_use_catalog_codes():
    """json_error("...") принимает только коды каталога, а не русский текст."""
    used = set()
    for path in (ROOT / "api").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        used |= set(re.findall(r'json_error\(\s*"([^"]+)"', text))
        used |= set(re.findall(r'Err\(\s*"([^"]+)"', text))
    for path in [ROOT / "domain" / "plan_storage.py"]:
        used |= set(re.findall(r'PlanStorageError\(\s*"([^"]+)"', path.read_text(encoding="utf-8")))
    assert used, "не нашли ни одного вызова — тест сломан"
    unknown = used - set(_CATALOG)
    assert not unknown, f"нет в каталоге: {unknown}"


def test_render_with_params_and_language():
    assert "25" in render_error("too_many_targets", "en", count=25, limit=20)
    assert render_error("plan_gone", "ru").startswith("План")
    assert render_error("plan_gone", "en").startswith("Plan")


def test_unknown_code_falls_back_to_code():
    assert render_error("no_such_code", "en") == "no_such_code"


def test_plan_storage_error_carries_code():
    exc = PlanStorageError("plan_limit", count=50)
    assert exc.code == "plan_limit" and exc.params == {"count": 50}


@pytest.fixture
def client():
    from api import create_app

    with create_app({"TESTING": True}).test_client() as test_client:
        yield test_client


class TestApiLanguage:
    def _post(self, client, headers=None, body=None):
        return client.post("/api/calculate", json=body or {}, headers=headers or {})

    def test_default_is_russian(self, client):
        r = self._post(client)
        assert r.status_code == 400
        assert "Отсутствует" in r.get_json()["message"]

    def test_accept_language_en(self, client):
        r = self._post(client, headers={"Accept-Language": "en"})
        assert "missing" in r.get_json()["message"]

    def test_body_lang_wins_over_header(self, client):
        r = self._post(client, headers={"Accept-Language": "ru"}, body={"lang": "en"})
        assert "missing" in r.get_json()["message"]
