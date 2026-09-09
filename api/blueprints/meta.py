"""
Метаданные приложения: версия, статус игрового сервера, состояние входа.

Всё здесь — ЧТЕНИЕ уже собранного. Ни один обработчик не ходит наружу:
счётчик онлайна кладёт в кэш scripts/refresh_server_status.py по
расписанию, а состояние входа определяется наличием настроек, а не
запросом к ESI.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint

from api.cache import json_ok, with_etag
from version import PHASE, VERSION

bp = Blueprint("meta", __name__)

ROOT = Path(__file__).resolve().parent.parent.parent
STATUS_SNAPSHOT = ROOT / "data" / "cache" / "server_status.json"

# Снапшот старше этого срока показываем как устаревший: лучше честно
# сказать «данные несвежие», чем выдавать вчерашний онлайн за текущий.
STALE_AFTER_MINUTES = 30


def _server_status() -> dict:
    if not STATUS_SNAPSHOT.is_file():
        return {
            "available": False,
            "reason": "no_snapshot",
            "hint": "Запустите scripts/refresh_server_status.py и поставьте его в расписание.",
        }

    try:
        snapshot = json.loads(STATUS_SNAPSHOT.read_text(encoding="utf-8"))
    except Exception:
        return {"available": False, "reason": "unreadable"}

    collected = snapshot.get("collected_at")
    age_minutes = None
    if collected:
        try:
            moment = datetime.fromisoformat(collected)
            age_minutes = int((datetime.now(timezone.utc) - moment).total_seconds() // 60)
        except ValueError:
            pass

    if snapshot.get("error"):
        return {"available": False, "reason": snapshot["error"], "age_minutes": age_minutes}

    return {
        "available": snapshot.get("players") is not None,
        "players": snapshot.get("players"),
        "server_version": snapshot.get("server_version"),
        "age_minutes": age_minutes,
        "stale": age_minutes is not None and age_minutes > STALE_AFTER_MINUTES,
    }


def _auth_status() -> dict:
    """
    Готова ли настоящая авторизация через EVE SSO.

    Проверяется наличие настроек приложения, а не обращение к ESI:
    без client_id вход невозможен в принципе, и об этом лучше сказать
    сразу, а не после неудачного перехода на страницу входа.
    """
    client_id = os.environ.get("EVE_CLIENT_ID", "").strip()
    return {
        "sso_configured": bool(client_id),
        "dev_mode": os.environ.get("PI_ENV", "dev").lower() == "dev",
        "hint": (
            "Задайте EVE_CLIENT_ID и EVE_CLIENT_SECRET из приложения на "
            "developers.eveonline.com. Пока их нет, персонажи берутся из "
            "dev-заглушек и реальные данные из игры не приходят."
        ),
    }


@bp.get("/meta")
def meta():
    """Версия, статус сервера, состояние входа — одним запросом."""
    return json_ok(
        version=VERSION,
        phase=PHASE,
        server=_server_status(),
        auth=_auth_status(),
    )


@bp.get("/characters")
def characters():
    """
    Персонажи, доступные планировщику.

    На Фазе 1 это dev-заглушки; в Фазе 3 то же место будет отдавать
    реальных персонажей, привязанных через SSO. Фронтенду разница
    не видна — поля те же.
    """
    from scripts.seed_dev_characters import load_characters

    crew = load_characters()
    return json_ok(
        characters=[
            {
                "character_id": c.character_id,
                "name": c.name,
                "ccu": c.command_center_upgrades_level,
                "ic": c.interplanetary_consolidation_level,
                "planet_slots": c.planet_slots,
                "source": "dev",
            }
            for c in crew
        ],
        source="dev" if crew else "none",
    )
