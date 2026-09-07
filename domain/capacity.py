"""
Расчёт загрузки CPU и Powergrid планеты по шаблонам застройки.

ИСТОЧНИК ДАННЫХ: data/pi_reference.json, выгружен из README репозитория
https://github.com/DalShooth/EVE_PI_Templates (раздел "Best Estimate
Template PG and CPU Usage"). Автор источника называет числа "best estimate";
их внутренняя согласованность проверена (суммы структур сходятся с
заявленными total, остатки — с ёмкостью CC по уровням CCU).

МОДЕЛЬ РАСЧЁТА (в v1 отсутствовала — там были константы 95/85/75):

    потребление = структуры шаблона(ов)
                + головы экстракторов (только для добывающего шаблона)
                + линки (зависят от РАДИУСА планеты)

    лимит       = ёмкость Command Center при уровне Command Center Upgrades

Ключевое следствие: одна и та же застройка влезает на маленькую планету
и не влезает на большую — потому что линки дорожают с радиусом. Именно
этот эффект readme v1 обещал ("radius penalties"), но код его не считал,
хотя колонку "Radius [km]" из CSV читал.

ДВА ШАБЛОНА НА ПЛАНЕТУ: для P2/P3 и P4 существуют варианты на 1 и на 2
фабрики. Вариант на 2 требует Command Center Upgrades V (min_ccu_level=5
в справочнике) — планировщик обязан это проверять, а не назначать вслепую.

ОГРАНИЧЕНИЯ ПО ТИПУ ПЛАНЕТЫ: P4-шаблоны ставятся только на Barren и
Temperate (allowed_planet_types в справочнике).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_REFERENCE_PATH = Path(__file__).resolve().parent.parent / "data" / "pi_reference.json"


@dataclass(frozen=True)
class Load:
    cpu: float
    pg: float

    def __add__(self, other: "Load") -> "Load":
        return Load(self.cpu + other.cpu, self.pg + other.pg)


@dataclass(frozen=True)
class Template:
    key: str
    label: str
    role: str            # "extraction" | "processing"
    produces_tier: str   # "P1" | "P2_P3" | "P4"
    factory_count: int   # 1 или 2 шаблона на планету
    min_ccu_level: int   # 5 для вариантов на 2 фабрики
    link_profile: str
    total: Load
    allowed_planet_types: tuple[str, ...] | None  # None = любая планета


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


class UnsupportedSetup(ValueError):
    """Комбинация шаблона, планеты и уровня скилла недопустима в принципе."""


@lru_cache(maxsize=1)
def _reference(path: Path = DEFAULT_REFERENCE_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_templates() -> dict[str, Template]:
    """Каталог шаблонов застройки из data/pi_reference.json."""
    raw = _reference()["templates"]
    result: dict[str, Template] = {}
    for key, t in raw.items():
        allowed = t.get("allowed_planet_types")
        result[key] = Template(
            key=key,
            label=t["label"],
            role=t["role"],
            produces_tier=t["produces_tier"],
            factory_count=t["factory_count"],
            min_ccu_level=t["min_ccu_level"],
            link_profile=t["link_profile"],
            total=Load(float(t["total_cpu"]), float(t["total_pg"])),
            allowed_planet_types=tuple(allowed) if allowed else None,
        )
    return result


def command_center_capacity(ccu_level: int) -> Load:
    """Доступные CPU/PG командного центра при данном уровне Command Center Upgrades."""
    table = _reference()["command_center_capacity"]
    key = str(int(ccu_level))
    if key not in table:
        raise UnsupportedSetup(f"Неизвестный уровень Command Center Upgrades: {ccu_level}")
    entry = table[key]
    return Load(float(entry["cpu"]), float(entry["pg"]))


def extractor_heads_load(head_count: int) -> Load:
    """
    Потребление голов экстрактора. Линейно по количеству
    (в источнике: x1 = 110/550, x10 = 1100/5500, x16 = 1760/8800).
    """
    if head_count < 0:
        raise ValueError("Количество голов экстрактора не может быть отрицательным")
    head = _reference()["extractor_head"]
    return Load(float(head["cpu"]) * head_count, float(head["pg"]) * head_count)


def link_load(link_profile: str, planet_radius_km: float) -> Load:
    """
    Потребление линков для профиля шаблона на планете заданного радиуса.

    Между узлами таблицы источника (шаг 2500 км) значения интерполируются
    линейно; за пределами таблицы берётся ближайший край с экстраполяцией
    по последнему шагу — это приближение, и оно помечено как таковое.

    Это первое место в проекте, где радиус планеты вообще влияет на расчёт:
    в v1 колонка "Radius [km]" читалась из CSV и не использовалась.
    """
    profiles = _reference()["link_usage_by_radius"]
    if link_profile not in profiles:
        raise UnsupportedSetup(f"Неизвестный профиль линков: {link_profile}")

    table = {int(r): v for r, v in profiles[link_profile].items() if r.isdigit()}
    radii = sorted(table)

    if planet_radius_km <= radii[0]:
        e = table[radii[0]]
        return Load(float(e["cpu"]), float(e["pg"]))
    if planet_radius_km >= radii[-1]:
        # Экстраполяция по наклону последнего интервала.
        r1, r2 = radii[-2], radii[-1]
        e1, e2 = table[r1], table[r2]
        step = (planet_radius_km - r2) / (r2 - r1)
        return Load(
            e2["cpu"] + (e2["cpu"] - e1["cpu"]) * step,
            e2["pg"] + (e2["pg"] - e1["pg"]) * step,
        )

    for r1, r2 in zip(radii, radii[1:]):
        if r1 <= planet_radius_km <= r2:
            e1, e2 = table[r1], table[r2]
            k = (planet_radius_km - r1) / (r2 - r1)
            return Load(
                e1["cpu"] + (e2["cpu"] - e1["cpu"]) * k,
                e1["pg"] + (e2["pg"] - e1["pg"]) * k,
            )
    raise UnsupportedSetup(f"Не удалось определить нагрузку линков для радиуса {planet_radius_km}")


def calculate_colony_load(
    template_key: str,
    ccu_level: int,
    planet_radius_km: float,
    planet_type: str | None = None,
    extractor_head_count: int = 0,
) -> ColonyLoad:
    """
    Полный расчёт загрузки планеты. Заменяет захардкоженные
    pg_load=95 / cpu_load=95 из main.py v1.

    Бросает UnsupportedSetup, если комбинация невозможна:
      - шаблон на 2 фабрики при CCU < 5;
      - P4-шаблон на планете не Barren/Temperate.
    Это именно ошибки конфигурации, а не "просто не влезло" —
    их нельзя маскировать высоким процентом загрузки.
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

    heads = extractor_heads_load(extractor_head_count) if extractor_head_count else Load(0.0, 0.0)

    return ColonyLoad(
        template_key=template_key,
        planet_radius_km=planet_radius_km,
        ccu_level=ccu_level,
        structures=tpl.total,
        extractor_heads=heads,
        links=link_load(tpl.link_profile, planet_radius_km),
        capacity=command_center_capacity(ccu_level),
    )


def max_planet_radius_that_fits(
    template_key: str,
    ccu_level: int,
    extractor_head_count: int = 0,
    step_km: int = 100,
    search_limit_km: int = 40000,
) -> float | None:
    """
    Наибольший радиус планеты, на котором застройка ещё влезает.

    Практическая функция для планировщика: позволяет заранее отсеять
    слишком большие планеты (в первую очередь Gas — в источнике прямо
    сказано, что для P2/P3 они не рекомендуются из-за размера),
    не перебирая их по одной.

    Возвращает None, если не влезает даже на минимальном радиусе.
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
