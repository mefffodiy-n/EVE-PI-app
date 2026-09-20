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

МОДЕЛЬ ДО 20.09.2026 (непрерывный ручеёк, ЗАМЕНЕНА): считала, что пока
тир активен, его выход течёт вниз по цепочке непрерывно, мелкими
партиями — то есть игрок способен возить сырьё между колониями сколько
угодно раз в сутки. Пользователь (владелец, реальный человек) прямо
возразил: он не может физически возить 24/7 крошечными партиями.

МОДЕЛЬ С 20.09.2026 — ПАРТИЯМИ-ЭСТАФЕТОЙ (согласована владельцем
проекта дословно: «нужно моделировать полную загрузку причала на
каждом тире и перемещение только после выемки всего готового
продукта»): игрок забирает готовую продукцию ОДНИМ рейсом только
когда причал ВЫШЕСТОЯЩЕГО тира ПОЛНОСТЬЮ переработан, везёт всю
партию сразу на причал следующего тира. Пока новая партия не
привезена — нижестоящий тир простаивает, даже если сам давно
освободился (а не «доступен непрерывно, пока производитель активен»,
как было раньше).

Общие для обеих версий модели данные (ничего не выдумывается):
  - ёмкость причала — одна и та же константа для любого тира
    (`data/pi_reference.json` → `storage_capacity_m3`, верифицирована
    скриншотом реального клиента 11.09.2026);
  - объём единицы товара — `data/cache/type_volumes.json`, собран
    `scripts/backfill_type_volumes.py` из публичного (без авторизации)
    эндпоинта ESI `/universe/types/{id}/`;
  - `logistics_refill_downtime_hours` (0.5ч простоя на одну довозку) —
    НЕ игровая константа, прямая оценка владельца проекта, не
    измерено (см. `data/pi_reference.json` → `assumptions.
    logistics_refill_downtime_hours`, статус "оценка пользователя").

ЧТО ЭТОТ МОДУЛЬ СЧИТАЕТ, А ЧТО — domain/throughput.py. Здесь только
«физика одной схемы»: расход её входов в м³/час на весь шаблон
колонии (own_consumption_profile). Каскадная арифметика партий-эстафеты
по всей цепочке (P1→P2→P3→P4: у кого сколько часов займёт переработать
ОДНУ полученную партию, когда придёт следующая, сколько единиц продукта
это даст) — в domain/throughput.py::_compute_duty_cycles(), которая
рекурсивно обходит дерево и явно этим занимается, потому что ей нужно
знать, кто чей вход (own_consumption_profile этого не знает).

ЧЕСТНЫЕ ПРОБЕЛЫ. Если для какого-то входа схемы нет данных об объёме
(кэш ещё не собран, новый товар) — вся схема целиком выходит из модели
(пустой профиль, входы перечислены в missing): правдоподобное, но
недостающее число хуже честного «не посчитано» (правило 1).

ПОЧЕМУ УЧИТЫВАЕТСЯ ЧИСЛО ФАБРИК, А НЕ ОДНА. Причал общий на весь
шаблон колонии, а не на одну фабрику: одну и ту же ёмкость опустошают
ВСЕ фабрики шаблона одновременно (12 advanced-фабрик на шаблон P2/P3,
8 high-tech на P4 — `domain/factory_site.py::FACTORIES_PER_TEMPLATE`,
из реальных игровых шаблонов). Без этого множителя расход в час
занижался в 8-12 раз, причал якобы опустошался за недели вместо
реальных ~2 суток (P1→P2) — найдено 20.09.2026 пользователем: план
показал ту же прибыль, что и до появления модели. Взят
однопричальный (не удвоенный) вариант шаблона — выбор одинарный/
двойной решается позже, на этапе подбора площадок под конкретную
планету, и здесь неизвестен; однопричальный — консервативная нижняя
оценка простоя, не завышающая проблему.

P1 (добывающий шаблон, `basic_industry_facility`) в эту модель
намеренно НЕ входит: его сырьё — то, что continuously добывает
собственный экстрактор ТОЙ ЖЕ колонии по внутренним маршрутам, а не
партия, которую физически привозит игрок с другой колонии. Простой
на логистику относится только к тирам, которые ждут груз ИЗВНЕ.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from domain.factory_site import FACTORIES_PER_TEMPLATE, TEMPLATE_BY_TIER

if TYPE_CHECKING:
    from domain.throughput import Schematic

ROOT = Path(__file__).resolve().parent.parent
REFERENCE_PATH = ROOT / "data" / "pi_reference.json"
TYPE_IDS_PATH = ROOT / "data" / "type_ids.json"
TYPE_VOLUMES_PATH = ROOT / "data" / "cache" / "type_volumes.json"

# Сколько фабрик ОДНОГО шаблона колонии делят между собой один причал —
# по категории структуры (Schematic.facility), не по тиру напрямую,
# потому что и P2, и P3 сидят на одном и том же advanced-шаблоне.
# basic_industry_facility (P1) сюда намеренно не входит — см. докстринг
# модуля: его причал не участвует в модели межколонийной логистики.
FACILITY_FACTORIES_PER_COLONY = {
    "advanced_industry_facility": FACTORIES_PER_TEMPLATE[TEMPLATE_BY_TIER["P2_P3"][1]],
    "high_tech_industry_facility": FACTORIES_PER_TEMPLATE[TEMPLATE_BY_TIER["P4"][1]],
}


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


def own_consumption_profile(schematic: "Schematic") -> tuple[dict[str, float], list[str]]:
    """
    Расход м³/час по каждому входу схемы, на ВСЕ фабрики её шаблона
    колонии — сырьё для каскада партий-эстафеты в domain/throughput.py.

    Возвращает (профиль, недостающие_объёмы_по_именам). Если для хотя
    бы одного входа схемы нет объёма — схема целиком выходит из модели:
    пустой словарь, все входы попадают в missing (правило 1 — не
    штрафовать наугад, честно "не посчитано" для всей схемы разом).
    P1 (basic_industry_facility) в модель не входит вовсе — пустой
    словарь, пустой missing: его причал не ждёт довозку с другой
    колонии, см. докстринг модуля.
    """
    factories_per_colony = FACILITY_FACTORIES_PER_COLONY.get(schematic.facility)
    if factories_per_colony is None:
        return {}, []

    profile: dict[str, float] = {}
    missing: list[str] = []
    for name in schematic.inputs:
        volume = volume_of(name)
        if volume is None:
            missing.append(name)
            continue
        profile[name] = schematic.input_per_hour(name) * factories_per_colony * volume

    if missing:
        return {}, missing

    return profile, []
