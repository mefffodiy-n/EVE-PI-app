"""
Сборщик статуса игрового сервера: сколько игроков онлайн.

ЗАЧЕМ ОТДЕЛЬНЫМ СКРИПТОМ. Правило проекта: пользователь не инициирует
обращений к ESI. Открытие страницы — действие пользователя, поэтому
счётчик онлайна нельзя запрашивать в обработчике HTTP. Его собирает
этот скрипт по расписанию, а приложение только читает снапшот.

Запуск вручную:
    python -m scripts.refresh_server_status

По расписанию (раз в 5-10 минут достаточно: число онлайна меняется
медленно, а лимиты ESI тратить незачем):
    Linux, cron:      */10 * * * * cd /path/to/app && python -m scripts.refresh_server_status
    Windows, планировщик заданий: то же самое действие раз в 10 минут.

Эндпоинт ESI: GET https://esi.evetech.net/latest/status/
Он публичный, авторизации не требует, и это единственный вызов, который
делает скрипт.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "cache"
SNAPSHOT = CACHE_DIR / "server_status.json"

ESI_STATUS_URL = "https://esi.evetech.net/latest/status/"
USER_AGENT = "PI-Director/0.4.0 (planetary industry planner; contact via repository)"
TIMEOUT_SECONDS = 10


def fetch() -> dict:
    """
    Забрать статус сервера.

    ESI просит представляться в User-Agent — без этого запросы могут
    ограничивать жёстче. Ошибку не проглатываем: пусть расписание
    покажет её в логе, а приложение продолжит отдавать прошлый снапшот.
    """
    request = urllib.request.Request(ESI_STATUS_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def save(payload: dict, error: str | None = None) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "players": payload.get("players"),
        "server_version": payload.get("server_version"),
        "start_time": payload.get("start_time"),
        "vip": payload.get("vip", False),
        "error": error,
    }
    SNAPSHOT.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    try:
        data = fetch()
    except urllib.error.HTTPError as exc:
        # 503 у ESI означает, что игровой сервер выключен — это не сбой
        # сборщика, а нормальное состояние во время ежедневного рестарта.
        message = "сервер офлайн" if exc.code == 503 else f"HTTP {exc.code}"
        save({}, error=message)
        print(f"Статус недоступен: {message}")
        return 0 if exc.code == 503 else 1
    except Exception as exc:
        save({}, error=f"{type(exc).__name__}: {exc}")
        print(f"Не удалось получить статус: {exc}")
        return 1

    save(data)
    print(f"Онлайн: {data.get('players')} · версия сервера {data.get('server_version')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
