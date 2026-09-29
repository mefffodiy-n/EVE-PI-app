"""
События для оповещений: дефицит добычи и истечение экстракторов.

Дефицит раньше считал только фронтенд (`extractorRatePerHour`,
`pinDeficitInfo`, `realChainProductsUsing` в web/app.js). Рассылка
оповещений — работа сборщика, у которого браузера нет, поэтому та же
логика живёт здесь, а фронтенд остаётся при своей копии; формулы
затухания сверены построчно и проверены на общих числах в
tests/test_alerts.py. Домен без сети и без БД: колонии приходят словарями
(как их отдаёт `Colony` в scripts/sync_colony_status.py).

Правила — прямое решение пользователя (11.09.2026): дефицит — добыча
экстрактора ниже 48 000 ед./ч; цепочка «под угрозой» называется только
если её прямо сейчас держит НАСТОЯЩАЯ колония (пин фабрики с продуктом,
потребляющим этот ресурс), расчётный план не считается.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from domain.recipes import load_recipes

DEFICIT_UNITS_PER_HOUR = 48000


def recipe_inputs() -> dict[str, list[str]]:
    """Что каждый продукт потребляет на входе (у P1 — R0-сырьё из `source`)."""
    result: dict[str, list[str]] = {}
    for r in load_recipes():
        if r.inputs:
            result[r.name] = sorted(r.inputs.keys())
        elif r.source:
            result[r.name] = [r.source]
    return result


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def extractor_decayed_qty(
    base_value: float | None, install_time: str | None, cycle_seconds: int | None, at: datetime
) -> int | None:
    """Затухшее значение цикла экстрактора на момент `at` (см. extractorDecayedQty в web/app.js)."""
    installed = _parse_iso(install_time)
    if base_value is None or installed is None or not cycle_seconds:
        return None
    time_diff = (at - installed).total_seconds()
    if not time_diff >= 0:
        return None
    cycle_num = max(math.floor((time_diff + 1) / cycle_seconds) - 1, 0)
    bar_width = cycle_seconds / 900
    t = (cycle_num + 0.5) * bar_width
    decay_value = base_value / (1 + t * 0.012)
    phase_shift = base_value ** 0.7
    sin_stuff = max(
        0.0,
        (math.cos(phase_shift + t / 12) + math.cos(phase_shift / 2 + t / 5) + math.cos(t / 2)) / 3,
    )
    return math.floor(bar_width * decay_value * (1 + 0.8 * sin_stuff))


def extractor_rate_per_hour(pin: dict[str, Any], at: datetime) -> float | None:
    qty = extractor_decayed_qty(
        pin.get("qty_per_cycle"), pin.get("install_time"), pin.get("cycle_seconds"), at
    )
    if qty is None:
        return None
    return qty * 3600 / pin["cycle_seconds"]


def _is_extractor(pin: dict[str, Any]) -> bool:
    return pin.get("kind") == "extractor_control_unit"


def real_chain_products_using(
    resource: str, colonies: list[dict[str, Any]], inputs: dict[str, list[str]]
) -> list[str]:
    products: set[str] = set()
    for colony in colonies:
        for pin in colony.get("pins") or []:
            kind = pin.get("kind") or ""
            product = pin.get("product")
            if kind.endswith("industry_facility") and product and resource in inputs.get(product, []):
                products.add(product)
    return sorted(products)


@dataclass(frozen=True)
class Deficit:
    character_id: int
    planet_id: int
    system: str
    planet_index: int
    pin_id: int | None
    product: str | None
    rate_per_hour: float
    at_risk: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Expiry:
    character_id: int
    planet_id: int
    system: str
    planet_index: int
    pin_id: int | None
    product: str | None
    expires_at: datetime
    expired: bool


def _where(colony: dict[str, Any]) -> tuple[int, int, str, int]:
    return (
        int(colony.get("character_id", 0)),
        int(colony.get("planet_id", 0)),
        str(colony.get("system_name") or ""),
        int(colony.get("planet_index") or 0),
    )


def find_deficits(
    colonies: list[dict[str, Any]], at: datetime, inputs: dict[str, list[str]] | None = None
) -> list[Deficit]:
    """Экстракторы, дающие сейчас меньше DEFICIT_UNITS_PER_HOUR. Неизвестная скорость — не дефицит."""
    inputs = recipe_inputs() if inputs is None else inputs
    found: list[Deficit] = []
    for colony in colonies:
        char_id, planet_id, system, index = _where(colony)
        for pin in colony.get("pins") or []:
            if not _is_extractor(pin):
                continue
            rate = extractor_rate_per_hour(pin, at)
            if rate is None or rate >= DEFICIT_UNITS_PER_HOUR:
                continue
            product = pin.get("product")
            risk = real_chain_products_using(product, colonies, inputs) if product else []
            found.append(
                Deficit(char_id, planet_id, system, index, pin.get("pin_id"), product, rate, risk)
            )
    return found


def find_expiring(
    colonies: list[dict[str, Any]], at: datetime, lead_hours: float
) -> list[Expiry]:
    """Экстракторы, программа которых закончилась или закончится в ближайшие `lead_hours`."""
    horizon = at + timedelta(hours=lead_hours)
    found: list[Expiry] = []
    for colony in colonies:
        char_id, planet_id, system, index = _where(colony)
        for pin in colony.get("pins") or []:
            if not _is_extractor(pin):
                continue
            expires = _parse_iso(pin.get("expiry_time"))
            if expires is None or expires > horizon:
                continue
            found.append(
                Expiry(char_id, planet_id, system, index, pin.get("pin_id"),
                       pin.get("product"), expires, expires <= at)
            )
    return found
