"""
Состояние данных приложения.

Пользователь должен видеть, НА КАКОЙ МОМЕНТ построен расчёт. Иначе
устаревшие данные выглядят так же, как свежие, и план строится по
позавчерашним ценам без единого намёка.

Эндпоинт только читает состояние файлов и кэша. Никаких обращений
наружу: правило проекта запрещает инициировать их пользовательским
запросом.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint

from api.cache import json_ok, plan_cache, with_etag

bp = Blueprint("status", __name__)

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data"

VERSION = "0.1.0"

# Насколько данные считаются свежими. Справочники статичны и не
# устаревают вовсе; рыночные цены живут минутами.
STALE_AFTER_HOURS = {
    "market_prices": 2,
    "colony_status": 24,
    "character_skills": 24,
}


def _age(path: Path) -> dict:
    """Возраст файла в часах и признак устаревания."""
    if not path.is_file():
        return {"present": False, "age_hours": None, "updated_at": None}
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    hours = (datetime.now(timezone.utc) - modified).total_seconds() / 3600
    return {
        "present": True,
        "age_hours": round(hours, 1),
        "updated_at": modified.isoformat(timespec="seconds"),
    }


@bp.get("/status")
@with_etag
def status():
    """
    Состояние данных: что загружено, когда обновлялось, чего не хватает.

    Фронтенд показывает это чипом в шапке, чтобы возраст данных был
    виден до того, как пользователь построит план.
    """
    reference = {
        "recipes": _age(DATA / "recipes.json"),
        "planets": _age(DATA / "planet_industry.csv"),
        "schematics": _age(DATA / "schematics.json"),
        "type_ids": _age(DATA / "type_ids.json"),
    }
    templates_dir = DATA / "templates"
    template_files = (
        [p for p in templates_dir.glob("*.json") if p.name != "miner_p1.json"]
        if templates_dir.is_dir()
        else []
    )

    missing = [name for name, info in reference.items() if not info["present"]]

    # Рыночные цены появятся в Фазе 2 вместе со сборщиком. Пока честно
    # сообщаем, что снапшота нет, вместо того чтобы молчать.
    market = _age(DATA / "market_snapshot.json")
    market["stale_after_hours"] = STALE_AFTER_HOURS["market_prices"]
    market["stale"] = bool(
        market["present"] and market["age_hours"] > STALE_AFTER_HOURS["market_prices"]
    )
    market["collector"] = "не реализован (Фаза 2)"

    products = 0
    if reference["recipes"]["present"]:
        products = len(json.loads((DATA / "recipes.json").read_text(encoding="utf-8")))

    return json_ok(
        version=VERSION,
        reference=reference,
        templates={"present": bool(template_files), "count": len(template_files)},
        products=products,
        market=market,
        missing=missing,
        plan_cache=plan_cache.stats(),
        ready=not missing,
    )
