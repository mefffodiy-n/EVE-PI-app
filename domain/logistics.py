"""
Пропускная способность причала между тирами переработки.

ЗАЧЕМ. Прогноз прибыльности плана (domain/poco_tax.py) до 20.09.2026
считал «полную загрузку цепочки без простоев» одинаково достижимой
для любого тира. По прямому запросу пользователя (владелец проекта,
считает реальные деньги на реальном плане): причал имеет ФИКСИРОВАННУЮ
ёмкость в м³ независимо от тира, но P2-рецепт обычно ест 2 вида P1,
а P3/P4 — обычно 3 вида P2/P3, каждый «тяжелее» на единицу. При той же
ёмкости причала это значит, что причал P2→P3 физически опустошается
быстрее, чем P1→P2, и фабрика простаивает в ожидании новой партии,
довезённой игроком.

МОДЕЛЬ (согласована владельцем проекта 20.09.2026, дословно):
  1. Ёмкость причала — одна и та же константа для любого тира.
     Различается только (а) сколько ЮНИТОВ ресурса в неё помещается
     (= ёмкость / объём единицы) и (б) сколько юнитов фабрика ест в
     час (Schematic.input_per_hour). Источники — оба уже есть в
     проекте, ничего не выдумывается:
       - ёмкость причала (`data/pi_reference.json` →
         `storage_capacity_m3`) — верифицирована скриншотом реального
         клиента (11.09.2026);
       - объём единицы товара (`data/cache/type_volumes.json`) —
         собран `scripts/backfill_type_volumes.py` из публичного (без
         авторизации) эндпоинта ESI `/universe/types/{id}/`.
  2. Логистика считается непрерывной и неограниченной (без отдельного
     ручного ввода «сколько раз в сутки вы возите»), но каждое
     опустошение причала стоит фиксированных `logistics_refill_
     downtime_hours` простоя, пока не привезли новую партию. Это
     НЕ игровая константа — прямая оценка владельца проекта, не
     измерено (см. `data/pi_reference.json` → `assumptions.
     logistics_refill_downtime_hours`, статус "оценка пользователя").

ЧЕСТНЫЕ ПРОБЕЛЫ. Если для какого-то входа схемы нет данных об объёме
(кэш ещё не собран, новый товар) — duty cycle для ЭТОЙ схемы считается
1.0 (без штрафа), а вход попадает в список недостающих: правдоподобное,
но недостающее число хуже честного «не посчитано» (правило 1).
Каскадный расчёт по всей цепочке (P1→P2→P3→P4) — в
domain/throughput.py::expand_demand(), которому здесь нужна только
функция для ОДНОЙ схемы.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain.throughput import Schematic

ROOT = Path(__file__).resolve().parent.parent
REFERENCE_PATH = ROOT / "data" / "pi_reference.json"
TYPE_IDS_PATH = ROOT / "data" / "type_ids.json"
TYPE_VOLUMES_PATH = ROOT / "data" / "cache" / "type_volumes.json"


@lru_cache(maxsize=1)
def _reference() -> dict:
    return json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def launchpad_capacity_m3() -> float:
    return float(_reference()["storage_capacity_m3"]["launchpad"])


@lru_cache(maxsize=1)
def logistics_downtime_hours() -> float:
    return float(_reference()["assumptions"]["logistics_refill_downtime_hours"]["value"])


@lru_cache(maxsize=1)
def _type_ids() -> dict[str, int]:
    if not TYPE_IDS_PATH.is_file():
        return {}
    return json.loads(TYPE_IDS_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _type_volumes() -> dict[str, float]:
    """{type_id как строка: объём м³} — снимок scripts/backfill_type_volumes.py.

    Читается напрямую, без сети (правило 3) — если файла ещё нет
    (свежий репозиторий, кэш не собран), возвращается пустой словарь и
    volume_of() честно отвечает None для всего, а не падает.
    """
    if not TYPE_VOLUMES_PATH.is_file():
        return {}
    try:
        return json.loads(TYPE_VOLUMES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def volume_of(product_name: str) -> float | None:
    """Объём одной единицы товара, м³ — None, если данных нет (честный пробел)."""
    type_id = _type_ids().get(product_name)
    if type_id is None:
        return None
    volume = _type_volumes().get(str(type_id))
    return float(volume) if volume is not None else None


def own_duty_cycle(schematic: "Schematic") -> tuple[float, list[str]]:
    """
    Доля времени, которое фабрика реально работает, а не простаивает в
    ожидании новой партии сырья в причале — см. докстринг модуля.

    Возвращает (duty_cycle, недостающие_объёмы_по_именам). Если для
    хотя бы одного входа схемы нет объёма — duty_cycle честно 1.0
    (без выдуманного штрафа), недостающие имена перечислены отдельно.
    """
    missing: list[str] = []
    total_m3_per_hour = 0.0
    for name in schematic.inputs:
        volume = volume_of(name)
        if volume is None:
            missing.append(name)
            continue
        total_m3_per_hour += schematic.input_per_hour(name) * volume

    if missing or total_m3_per_hour <= 0:
        return 1.0, missing

    drain_hours = launchpad_capacity_m3() / total_m3_per_hour
    downtime = logistics_downtime_hours()
    return drain_hours / (drain_hours + downtime), missing
