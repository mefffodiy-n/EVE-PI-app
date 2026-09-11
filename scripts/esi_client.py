"""
Общий клиент ESI для фоновых сборщиков.

Единственное место в проекте, откуда идут обращения к API EVE. Собран
по официальной документации developers.eveonline.com, раздел
«Services and Resources → ESI».

ЧТО ЗДЕСЬ СОБЛЮДАЕТСЯ И ПОЧЕМУ:

1. X-Compatibility-Date. ESI больше не версионируется путями вида
   /latest/ или /v1/ — вместо этого запрос сообщает дату, на которую
   приложение было проверено. Если заголовок не передать, применяется
   САМАЯ СТАРАЯ доступная версия поведения, а её порог CCP периодически
   поднимает — то есть приложение однажды сломается без предупреждения.

   Дата здесь ЗАФИКСИРОВАНА константой, а не берётся текущая. Смысл
   заголовка именно в этом: «поведение, которое мы проверили». Подставлять
   сегодняшнюю дату — значит соглашаться на любые будущие изменения
   вслепую, что ровно та проблема, от которой заголовок защищает.
   Дату меняют вручную, сверившись с изменениями.

2. User-Agent с контактом. CCP просит представляться, чтобы иметь
   возможность связаться с автором вместо блокировки. Без внятного
   User-Agent запросы ограничивают жёстче.

3. Лимит ошибок. Сотня неуспешных ответов за минуту — и ESI отвечает
   420 на ВСЕ маршруты до конца окна, независимо от того, были бы они
   успешными. Поэтому заголовки X-ESI-Error-Limit-Remain и
   X-ESI-Error-Limit-Reset читаются, и при исчерпании клиент сам
   перестаёт слать запросы, не дожидаясь бана.

4. Ответ 429 с Retry-After. Новый лимит по токенам работает скользящим
   окном на пару «приложение + персонаж». Retry-After говорит, через
   сколько секунд запрос пройдёт; повторяем не раньше.

5. ETag. Ответ 304 не тратит трафик и не считается ошибкой. Для данных,
   которые меняются редко, это дешевле повторной выкачки.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ETAG_STORE = ROOT / "data" / "cache" / "etags.json"

BASE_URL = "https://esi.evetech.net"

# Дата, на которую поведение ESI было проверено для этого приложения.
# МЕНЯТЬ ВРУЧНУЮ, сверившись с изменениями в дев-блогах, а не
# автоматически: в этом весь смысл заголовка.
COMPATIBILITY_DATE = "2025-08-26"

# Собирается из version.py: единственное место, где живёт номер версии
# и контакт. Раньше строка была вписана здесь и в сборщике цен, и версии
# в них разошлись.
def _user_agent() -> str:
    import sys
    sys.path.insert(0, str(ROOT))
    from version import user_agent
    return user_agent()


USER_AGENT = _user_agent()

TIMEOUT_SECONDS = 15

# Запас по лимиту ошибок: перестаём слать запросы, не дойдя до нуля.
# Ноль означает 420 на всех маршрутах, включая исправные.
ERROR_LIMIT_FLOOR = 10


class EsiError(RuntimeError):
    """Запрос к ESI не удался."""


class EsiRateLimited(EsiError):
    """Лимит исчерпан — обращаться нельзя до истечения окна."""

    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class EsiResponse:
    status: int
    data: object | None
    headers: dict = field(default_factory=dict)
    from_cache: bool = False       # пришёл 304, тело не менялось

    @property
    def ok(self) -> bool:
        return self.status < 400


class EsiClient:
    """
    Клиент для фоновых сборщиков.

    Хранит состояние лимитов между вызовами: если предыдущий запрос
    показал, что окно ошибок почти исчерпано, следующий не отправляется.
    """

    def __init__(self, user_agent: str = USER_AGENT,
                 compatibility_date: str = COMPATIBILITY_DATE,
                 opener=None):
        self.user_agent = user_agent
        self.compatibility_date = compatibility_date
        self._opener = opener            # для тестов, чтобы не ходить в сеть
        self._blocked_until = 0.0
        self._errors_remaining: int | None = None
        self._etags = self._load_etags()

    # ── Хранение ETag ────────────────────────────────────────────
    def _load_etags(self) -> dict:
        if not ETAG_STORE.is_file():
            return {}
        try:
            return json.loads(ETAG_STORE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_etags(self) -> None:
        ETAG_STORE.parent.mkdir(parents=True, exist_ok=True)
        ETAG_STORE.write_text(
            json.dumps(self._etags, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    # ── Состояние лимитов ────────────────────────────────────────
    @property
    def blocked_for(self) -> int:
        """Сколько секунд осталось до снятия самоблокировки."""
        return max(0, int(self._blocked_until - time.time()))

    def _note_limits(self, headers: dict) -> None:
        remain = headers.get("X-ESI-Error-Limit-Remain")
        reset = headers.get("X-ESI-Error-Limit-Reset")
        if remain is None:
            return
        try:
            self._errors_remaining = int(remain)
            seconds = int(reset) if reset is not None else 60
        except (TypeError, ValueError):
            return

        if self._errors_remaining <= ERROR_LIMIT_FLOOR:
            # Не дожидаемся нуля: ноль означает 420 на всех маршрутах.
            self._blocked_until = time.time() + seconds

    # ── Запрос ───────────────────────────────────────────────────
    def get(self, path: str, use_etag: bool = True, token: str | None = None) -> EsiResponse:
        """
        Выполнить GET к маршруту ESI.

        path  — часть после домена, например "/status/".
        token — access-токен персонажа для авторизованных маршрутов
                (скиллы, колонии). Публичные маршруты передают None.
        """
        if self.blocked_for:
            raise EsiRateLimited(
                f"Лимит ошибок ESI почти исчерпан, пауза ещё {self.blocked_for} с",
                self.blocked_for,
            )

        url = f"{BASE_URL}{path}"
        headers = {
            "User-Agent": self.user_agent,
            "X-Compatibility-Date": self.compatibility_date,
            "Accept": "application/json",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if use_etag and url in self._etags:
            headers["If-None-Match"] = self._etags[url]

        try:
            if self._opener is not None:
                status, body, response_headers = self._opener(url, headers)
            else:
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                    status = response.status
                    body = response.read().decode("utf-8")
                    response_headers = dict(response.headers)
        except urllib.error.HTTPError as exc:
            response_headers = dict(exc.headers or {})
            self._note_limits(response_headers)

            if exc.code == 304:
                return EsiResponse(304, None, response_headers, from_cache=True)

            if exc.code in (420, 429):
                retry = int(response_headers.get("Retry-After")
                            or response_headers.get("X-ESI-Error-Limit-Reset") or 60)
                self._blocked_until = time.time() + retry
                raise EsiRateLimited(
                    f"ESI ограничил обращения (HTTP {exc.code}), повтор через {retry} с",
                    retry,
                ) from exc

            raise EsiError(f"HTTP {exc.code} на {path}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise EsiError(f"Сеть недоступна: {exc}") from exc

        self._note_limits(response_headers)

        if status == 304:
            return EsiResponse(304, None, response_headers, from_cache=True)

        # Путь через opener (тесты) не бросает HTTPError сам — приводим
        # к тому же поведению, что и реальный urlopen.
        if status in (420, 429):
            retry = int(response_headers.get("Retry-After")
                        or response_headers.get("X-ESI-Error-Limit-Reset") or 60)
            self._blocked_until = time.time() + retry
            raise EsiRateLimited(
                f"ESI ограничил обращения (HTTP {status}), повтор через {retry} с", retry
            )
        if status >= 400:
            raise EsiError(f"HTTP {status} на {path}")

        etag = response_headers.get("ETag")
        if use_etag and etag:
            self._etags[url] = etag
            self._save_etags()

        try:
            data = json.loads(body)
        except ValueError as exc:
            raise EsiError(f"Ответ не является JSON: {exc}") from exc

        return EsiResponse(status, data, response_headers)

    # ── POST ─────────────────────────────────────────────────────
    def post(self, path: str, payload: object) -> EsiResponse:
        """
        POST к ESI. Сейчас только для `/universe/ids/` — резолвинг имя
        предмета -> type_id. Разовая задача проверки данных (см.
        scripts/resolve_pi_structure_type_ids.py), не рантайм-путь
        сборщиков, поэтому без ETag (эндпоинт его не отдаёт) и без
        opener-инъекции для тестов get(). Лимит ошибок и правила 420/429
        те же, что и у get() — тело дублирует его обработку намеренно,
        чтобы не усложнять общий путь ради разового вызова.
        """
        if self.blocked_for:
            raise EsiRateLimited(
                f"Лимит ошибок ESI почти исчерпан, пауза ещё {self.blocked_for} с",
                self.blocked_for,
            )

        url = f"{BASE_URL}{path}"
        headers = {
            "User-Agent": self.user_agent,
            "X-Compatibility-Date": self.compatibility_date,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        body = json.dumps(payload).encode("utf-8")

        try:
            request = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                status = response.status
                resp_body = response.read().decode("utf-8")
                response_headers = dict(response.headers)
        except urllib.error.HTTPError as exc:
            response_headers = dict(exc.headers or {})
            self._note_limits(response_headers)
            if exc.code in (420, 429):
                retry = int(response_headers.get("Retry-After")
                            or response_headers.get("X-ESI-Error-Limit-Reset") or 60)
                self._blocked_until = time.time() + retry
                raise EsiRateLimited(
                    f"ESI ограничил обращения (HTTP {exc.code}), повтор через {retry} с",
                    retry,
                ) from exc
            raise EsiError(f"HTTP {exc.code} на {path}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise EsiError(f"Сеть недоступна: {exc}") from exc

        self._note_limits(response_headers)
        if status >= 400:
            raise EsiError(f"HTTP {status} на {path}")

        try:
            data = json.loads(resp_body)
        except ValueError as exc:
            raise EsiError(f"Ответ не является JSON: {exc}") from exc

        return EsiResponse(status, data, response_headers)


def suggested_compatibility_date() -> str:
    """
    Какую дату можно было бы поставить сегодня.

    Полезна при плановой сверке: документация указывает, что версия API
    меняется в 11:00 UTC, поэтому «сегодняшняя» дата — это now() минус
    11 часов. Значение НЕ подставляется автоматически: фиксация даты
    и есть защита от неожиданных изменений.
    """
    return (datetime.now(timezone.utc) - timedelta(hours=11)).date().isoformat()
