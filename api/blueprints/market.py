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


def buy_prices(raw_prices: dict) -> dict[str, float]:
    """Цены покупки (buy_max) из снимка: за неё товар можно продать сразу — оценка консервативна."""
    return {
        name: entry["buy_max"]
        for name, entry in raw_prices.items()
        if isinstance(entry, dict) and entry.get("buy_max")
    }


def load_buy_prices() -> dict[str, float]:
    """Цены покупки из снимка; при любой проблеме со снимком — пустой словарь."""
    try:
        return buy_prices((_load_snapshot() or {}).get("prices") or {})
    except Exception:
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


@bp.post("/plan-revenue-trend")
def plan_revenue_trend():
    """
    Динамика выручки плана и цен его целевых продуктов за `days` суток
    (по умолчанию 30, максимум 90) — из истории, которую копит сборщик
    цен. Строки — [{product, monthly_units}] из revenue_by_product.
    """
    from api.cache import json_error, parse_json_body
    from domain import price_history
    from infra.db import session_scope

    payload, error = parse_json_body({"items": list})
    if error:
        return json_error(error)
    items = payload["items"]
    if not all(isinstance(i, dict) for i in items):
        return json_error("items_not_objects", field="items")
    try:
        days = max(2, min(price_history.KEEP_DAYS, int(payload.get("days", 30))))
    except (TypeError, ValueError):
        days = 30
    station = str((_load_snapshot() or {}).get("station") or "jita")
    with session_scope() as session:
        result = price_history.revenue_trend(session, station, items[:50], days)
    return json_ok(**result, station=station)


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
    prices = buy_prices(raw_prices)

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
