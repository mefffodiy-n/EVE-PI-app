"""
Рыночные данные и оценка выгоды — ЧТЕНИЕ СНИМКА, без внешних вызовов.

Контракт фронтенда:
  GET /api/market/best-product
    -> {status, recommended, analytics[...], snapshot{...}}

Как было в первой версии: кнопка в интерфейсе заставляла сервер идти
на market.fuzzwork.co.uk прямо в обработчике запроса, а «рекомендацией»
служил максимум цены за единицу с ручным исключением Water и Oxygen.
Цена за единицу несопоставима между тирами, и такая рекомендация ничего
не значила.

Как здесь: цены собирает scripts/refresh_market_prices.py по расписанию,
а ранжирование считает domain/profit.py в ISK на колонию в час —
ограниченный ресурс здесь планеты и персонажи, а не время.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, request

from api.cache import json_ok

bp = Blueprint("market", __name__)

ROOT = Path(__file__).resolve().parent.parent.parent
SNAPSHOT = ROOT / "data" / "cache" / "market_prices.json"

# Снимок старше суток помечается как устаревший: цены на PI устойчивы,
# но выдавать недельные за свежие нельзя.
STALE_AFTER_MINUTES = 24 * 60


def _load_snapshot() -> dict:
    if not SNAPSHOT.is_file():
        return {}
    try:
        return json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _age_minutes(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        taken = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if taken.tzinfo is None:
        taken = taken.replace(tzinfo=timezone.utc)
    return int((datetime.now(timezone.utc) - taken).total_seconds() // 60)


@bp.get("/market/best-product")
def best_product():
    """
    Что выгоднее производить по последнему снимку цен.

    Параметр planet_slots (по умолчанию 6) задаёт число планет на
    персонажа для колонки «ISK на персонажа в час».
    """
    from domain.profit import rank

    snapshot = _load_snapshot()
    raw_prices = snapshot.get("prices") or {}

    if not raw_prices:
        return json_ok(
            recommended=None,
            analytics=[],
            snapshot={"has_data": False},
            note=(
                "Снимок рыночных цен ещё не собран. Запустите "
                "python -m scripts.refresh_market_prices — или планировщик "
                "python -m scripts.scheduler, который делает это по расписанию."
            ),
        )

    # Берём цену покупки: за неё товар можно продать немедленно.
    # Оценка получается консервативной, и это правильнее завышенной.
    prices = {
        name: entry["buy_max"]
        for name, entry in raw_prices.items()
        if isinstance(entry, dict) and entry.get("buy_max")
    }

    try:
        slots = max(1, min(6, int(request.args.get("planet_slots", 6))))
    except ValueError:
        slots = 6

    chains = rank(prices, planet_slots=slots)
    priced = [c for c in chains if c.isk_per_colony_hour is not None]

    age = _age_minutes(snapshot.get("collected_at"))
    return json_ok(
        recommended=priced[0].to_dict() if priced else None,
        analytics=[c.to_dict() for c in chains],
        snapshot={
            "has_data": True,
            "collected_at": snapshot.get("collected_at"),
            "age_minutes": age,
            "stale": age is not None and age > STALE_AFTER_MINUTES,
            "station": snapshot.get("station"),
            "source": snapshot.get("source"),
            "priced_products": len(priced),
            "total_products": len(chains),
        },
        note=(
            "Ранжировано по ISK на колонию в час: ограниченный ресурс — "
            "планеты и персонажи. Налог POCO, доставка и биржевые сборы "
            "не учтены, поэтому числа — верхняя оценка."
        ),
    )
