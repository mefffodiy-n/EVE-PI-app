"""
Расчёт загрузки CPU и Powergrid планеты.

ИСТОЧНИК: data/pi_reference.json (структуры, ёмкость CC, модель линков),
выведено из https://github.com/DalShooth/EVE_PI_Templates и разбора
реальных JSON-шаблонов (см. domain/templates.py).

МОДЕЛЬ:
    потребление = сумма(структуры) + головы экстракторов + линки(радиус)
    лимит       = ёмкость Command Center при уровне Command Center Upgrades

Линки считаются ПО ФОРМУЛЕ, а не интерполяцией таблиц:
    L   = 0.012 * radius + 1          (длина линка)
    cpu = (15  + 0.2  * L) * link_count
    pg  = (10  + 0.15 * L) * link_count
Формула воспроизводит все 60 точек пяти таблиц README с отклонением < 1
(округление) и, в отличие от таблиц, работает при любом числе линков —
в частности для 00-шаблона майнера с 10 линками, которого в README нет.

ЧТО ИЗМЕНИЛОСЬ ОТНОСИТЕЛЬНО v1: там pg_load/cpu_load были константами
95/85/75 независимо от застройки, планеты и скиллов.

ОГРАНИЧЕНИЯ, которые проверяются явно:
  - шаблон на 2 фабрики требует Command Center Upgrades V;
  - P4-шаблоны ставятся только на Barren и Temperate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_REFERENCE_PATH = Path(__file__).resolve().parent.parent / "data" / "pi_reference.json"


class UnsupportedSetup(ValueError):
    """Комбинация шаблона, планеты и уровня скилла недопустима в принципе."""


@dataclass(frozen=True)
class Load:
    cpu: float
    pg: float

    def __add__(self, other: "Load") -> "Load":
        return Load(self.cpu + other.cpu, self.pg + other.pg)

    def __mul__(self, k: float) -> "Load":
        return Load(self.cpu * k, self.pg * k)


@dataclass(frozen=True)
class Template:
    key: str
    label: str
    role: str                 # "extraction" | "processing"
    produces_tier: str        # "P1" | "P2_P3" | "P4"
    template_count: int       # сколько шаблонов ставится на планету (1 или 2)
    min_ccu_level: int
    link_count: int
    extractor_heads: int
    structures: dict[str, int]
    allowed_planet_types: tuple[str, ...] | None


@dataclass(frozen=True)
class ColonyLoad:
    template_key: str
    planet_radius_km: float
    ccu_level: int
    structures: Load
    extractor_heads: Load
    links: Load
    capacity: Load

    @property
    def used(self) -> Load:
        return self.structures + self.extractor_heads + self.links

    @property
    def cpu_percent(self) -> float:
        return 0.0 if self.capacity.cpu == 0 else round(100 * self.used.cpu / self.capacity.cpu, 1)

    @property
    def pg_percent(self) -> float:
        return 0.0 if self.capacity.pg == 0 else round(100 * self.used.pg / self.capacity.pg, 1)

    @property
    def fits(self) -> bool:
        return self.used.cpu <= self.capacity.cpu and self.used.pg <= self.capacity.pg


@lru_cache(maxsize=1)
def _reference(path: Path = DEFAULT_REFERENCE_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_templates() -> dict[str, Template]:
    """Каталог шаблонов застройки."""
    raw = _reference()["templates"]
    result: dict[str, Template] = {}
    for key, t in raw.items():
        if key.startswith("_"):
            continue
        allowed = t.get("allowed_planet_types")
        result[key] = Template(
            key=key,
            label=t["label"],
            role=t["role"],
            produces_tier=t["produces_tier"],
            template_count=int(t["template_count"]),
            min_ccu_level=int(t["min_ccu_level"]),
            link_count=int(t["link_count"]),
            extractor_heads=int(t.get("extractor_heads", 0)),
            structures=dict(t["structures"]),
            allowed_planet_types=tuple(allowed) if allowed else None,
        )
    return result


def command_center_capacity(ccu_level: int) -> Load:
    table = _reference()["command_center_capacity"]
    key = str(int(ccu_level))
    if key not in table:
        raise UnsupportedSetup(f"Неизвестный уровень Command Center Upgrades: {ccu_level}")
    e = table[key]
    return Load(float(e["cpu"]), float(e["pg"]))


def structures_load(structures: dict[str, int]) -> Load:
    """Суммарное потребление набора структур."""
    catalog = _reference()["structures"]
    total = Load(0.0, 0.0)
    for name, count in structures.items():
        if name not in catalog:
            raise UnsupportedSetup(f"Неизвестная структура: {name}")
        unit = catalog[name]
        total = total + Load(float(unit["cpu"]) * count, float(unit["pg"]) * count)
    return total


def extractor_heads_load(head_count: int) -> Load:
    if head_count < 0:
        raise ValueError("Количество голов экстрактора не может быть отрицательным")
    head = _reference()["structures"]["extractor_head"]
    return Load(float(head["cpu"]) * head_count, float(head["pg"]) * head_count)


def link_length(planet_radius_km: float) -> float:
    m = _reference()["link_model"]
    return m["length_per_radius_km"] * planet_radius_km + m["length_offset"]


def link_load(link_count: int, planet_radius_km: float) -> Load:
    """
    Потребление всех линков шаблона.

    Первое место в проекте, где радиус планеты вообще влияет на расчёт:
    в v1 колонка "Radius [km]" читалась из CSV и не использовалась.
    """
    m = _reference()["link_model"]
    length = link_length(planet_radius_km)
    per_cpu = m["cpu_base"] + m["cpu_per_length"] * length
    per_pg = m["pg_base"] + m["pg_per_length"] * length
    return Load(per_cpu * link_count, per_pg * link_count)


def calculate_colony_load(
    template_key: str,
    ccu_level: int,
    planet_radius_km: float,
    planet_type: str | None = None,
    extractor_head_count: int | None = None,
) -> ColonyLoad:
    """
    Полный расчёт загрузки планеты по шаблону застройки.

    extractor_head_count=None -> берётся значение из шаблона
    (для 00-майнера это 10 голов).
    """
    templates = load_templates()
    if template_key not in templates:
        raise UnsupportedSetup(f"Неизвестный шаблон: {template_key}")
    tpl = templates[template_key]

    if ccu_level < tpl.min_ccu_level:
        raise UnsupportedSetup(
            f"Шаблон '{tpl.label}' требует Command Center Upgrades "
            f"{tpl.min_ccu_level}, у персонажа {ccu_level}"
        )

    if tpl.allowed_planet_types and planet_type is not None:
        if planet_type not in tpl.allowed_planet_types:
            raise UnsupportedSetup(
                f"Шаблон '{tpl.label}' нельзя поставить на планету типа "
                f"'{planet_type}' (допустимы: {', '.join(tpl.allowed_planet_types)})"
            )

    heads = tpl.extractor_heads if extractor_head_count is None else extractor_head_count

    return ColonyLoad(
        template_key=template_key,
        planet_radius_km=planet_radius_km,
        ccu_level=ccu_level,
        structures=structures_load(tpl.structures),
        extractor_heads=extractor_heads_load(heads),
        links=link_load(tpl.link_count, planet_radius_km),
        capacity=command_center_capacity(ccu_level),
    )


def max_planet_radius_that_fits(
    template_key: str,
    ccu_level: int,
    extractor_head_count: int | None = None,
    step_km: int = 100,
    search_limit_km: int = 200000,
) -> float | None:
    """
    Наибольший радиус планеты, на котором застройка ещё влезает.

    Нужна планировщику, чтобы заранее отсеивать слишком большие планеты
    (в первую очередь Gas — в источнике прямо сказано, что для P2/P3 они
    не рекомендуются из-за размера). None — не влезает нигде.
    """
    last_ok: float | None = None
    for radius in range(step_km, search_limit_km + step_km, step_km):
        load = calculate_colony_load(
            template_key, ccu_level, float(radius), extractor_head_count=extractor_head_count
        )
        if load.fits:
            last_ok = float(radius)
        else:
            break
    return last_ok


def available_templates_for(ccu_level: int, planet_type: str | None = None) -> list[Template]:
    """
    Какие шаблоны доступны персонажу с данной прокачкой на данной планете.

    Именно здесь реализовано правило «2 шаблона на планету только при CCU V»:
    у персонажа с CCU IV варианты *_2factory просто не попадут в выдачу.
    """
    result = []
    for tpl in load_templates().values():
        if ccu_level < tpl.min_ccu_level:
            continue
        if tpl.allowed_planet_types and planet_type is not None:
            if planet_type not in tpl.allowed_planet_types:
                continue
        result.append(tpl)
    return result
