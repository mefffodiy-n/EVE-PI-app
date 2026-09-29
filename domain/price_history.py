"""
История рыночных цен и динамика выручки плана.

Снимок `data/cache/market_prices.json` перезаписывается каждый час, старые
цены в нём не остаются. Сборщик (scripts/refresh_market_prices.py) теперь
дописывает каждый снимок в таблицу `price_samples`; здесь — чтение и
сведение в дневные ряды. Ничего не ходит в сеть (правило 3).

Дневной ряд — ПОСЛЕДНЯЯ цена покупки (buy max) за сутки UTC. Динамика
выручки плана считается только по целевым продуктам плана: выручка =
Σ (штук в месяц × цена покупки в этот день). День, в котором цены нет
хотя бы у одного продукта, пропускается, а не считается с нулём или
соседней ценой (правило 1).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from infra.models import PriceSample

KEEP_DAYS = 90
TYPE_IDS_PATH = Path(__file__).resolve().parent.parent / "data" / "type_ids.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def record(
    session: Session,
    prices: dict[str, dict],
    station: str,
    type_ids: dict[str, int],
    now: datetime | None = None,
) -> int:
    """Дописать снимок цен (имя продукта -> {buy_max, sell_min}). Возвращает число строк."""
    stamp = now or _now()
    count = 0
    for name, entry in prices.items():
        type_id = type_ids.get(name)
        if type_id is None or not isinstance(entry, dict):
            continue
        buy, sell = entry.get("buy_max"), entry.get("sell_min")
        if not buy and not sell:
            continue
        session.add(
            PriceSample(
                station=station, product_type_id=type_id,
                buy_max=buy or None, sell_min=sell or None, sampled_at=stamp,
            )
        )
        count += 1
    return count


def prune(session: Session, now: datetime | None = None, keep_days: int = KEEP_DAYS) -> int:
    cutoff = (now or _now()) - timedelta(days=keep_days)
    result = session.execute(delete(PriceSample).where(PriceSample.sampled_at < cutoff))
    return getattr(result, "rowcount", 0) or 0


def daily_buy_series(
    session: Session, station: str, type_id: int, days: int, now: datetime | None = None
) -> dict[date, float]:
    """{дата UTC: последняя цена покупки за сутки} за последние `days` суток."""
    cutoff = (now or _now()) - timedelta(days=days)
    rows = session.execute(
        select(PriceSample.sampled_at, PriceSample.buy_max)
        .where(
            PriceSample.station == station,
            PriceSample.product_type_id == type_id,
            PriceSample.sampled_at >= cutoff,
            PriceSample.buy_max.is_not(None),
        )
        .order_by(PriceSample.sampled_at)
    ).all()
    series: dict[date, float] = {}
    for sampled_at, buy in rows:
        series[sampled_at.astimezone(timezone.utc).date()] = float(buy)
    return series


def revenue_trend(
    session: Session,
    station: str,
    items: list[dict[str, Any]],
    days: int,
    type_ids: dict[str, int] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    items — [{product, monthly_units}]. Ответ:
      by_product — {продукт: [{date, price}]} (дневной ряд цены покупки),
      points     — [{date, revenue}] (дни, где есть цены на ВСЕ продукты),
      change_pct — изменение выручки от первой до последней точки (None, если точек < 2).
    """
    if type_ids is None:
        type_ids = json.loads(TYPE_IDS_PATH.read_text(encoding="utf-8"))
    per_product: dict[str, dict[date, float]] = {}
    units: dict[str, float] = {}
    for item in items:
        name = str(item.get("product", ""))
        try:
            qty = float(item.get("monthly_units") or 0)
        except (TypeError, ValueError):
            continue
        type_id = type_ids.get(name)
        if type_id is None or qty <= 0:
            continue
        per_product[name] = daily_buy_series(session, station, type_id, days, now)
        units[name] = qty

    by_product = {
        name: [{"date": d.isoformat(), "price": p} for d, p in sorted(series.items())]
        for name, series in per_product.items()
    }
    points: list[dict[str, Any]] = []
    if per_product:
        all_days = sorted(set().union(*(s.keys() for s in per_product.values())))
        for day in all_days:
            if all(day in s for s in per_product.values()):
                revenue = sum(units[n] * per_product[n][day] for n in per_product)
                points.append({"date": day.isoformat(), "revenue": revenue})
    change = None
    if len(points) >= 2 and points[0]["revenue"] > 0:
        change = round(100 * (points[-1]["revenue"] / points[0]["revenue"] - 1), 1)
    return {"by_product": by_product, "points": points, "change_pct": change, "days": days}
