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

Эндпоинт ESI: GET /status/ — публичный, авторизации не требует.

Обращение идёт через scripts/esi_client.py, который соблюдает правила
CCP: представляется в User-Agent, передаёт X-Compatibility-Date, следит
за лимитом ошибок и уважает Retry-After. Раньше здесь был прямой вызов
на /latest/status/ — путь со старым способом версионирования, без
заголовка совместимости. Такой запрос обслуживается по самой старой
доступной версии поведения, а её порог CCP периодически поднимает.
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

ESI_STATUS_PATH = "/status/"


def fetch() -> dict | None:
    """
    Забрать статус сервера.

    Возвращает None, если ESI ответил 304: тело не изменилось с прошлого
    раза, и перезаписывать снимок нечем. Это не ошибка, а экономия —
    трафик не тратится, а лимит не расходуется.

    Ошибку не проглатываем: пусть расписание покажет её в логе,
    а приложение продолжит отдавать прошлый снимок.
    """
    from scripts.esi_client import EsiClient

    response = EsiClient().get(ESI_STATUS_PATH)
    return None if response.from_cache else response.data


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
    from scripts.esi_client import EsiError, EsiRateLimited

    try:
        data = fetch()
    except EsiRateLimited as exc:
        # Не ошибка данных, а просьба подождать. Прошлый снимок не трогаем:
        # он устареет, и приложение честно покажет его возраст.
        print(f"ESI просит подождать: {exc}")
        return 1
    except EsiError as exc:
        message = str(exc)
        # 503 у ESI означает, что игровой сервер выключен — это не сбой
        # сборщика, а нормальное состояние во время ежедневного рестарта.
        if "503" in message:
            save({}, error="сервер офлайн")
            print("Статус недоступен: сервер офлайн")
            return 0
        save({}, error=message)
        print(f"Не удалось получить статус: {message}")
        return 1
    except Exception as exc:
        save({}, error=f"{type(exc).__name__}: {exc}")
        print(f"Не удалось получить статус: {exc}")
        return 1

    if data is None:
        # 304: тело не менялось, перезаписывать снимок нечем.
        # Обновляем только метку времени, чтобы возраст был честным.
        print("Статус не изменился с прошлого раза (304) — снимок актуален")
        return _touch()

    save(data)
    print(f"Онлайн: {data.get('players')} · версия сервера {data.get('server_version')}")
    return 0


def _touch() -> int:
    """Обновить время сбора у существующего снимка после ответа 304."""
    if not SNAPSHOT.is_file():
        return 0
    try:
        snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 1
    snapshot["collected_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    SNAPSHOT.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
