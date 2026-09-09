"""
Разбор JSON-шаблонов планетарной застройки EVE Online.

Формат файла (реальный игровой формат, разобран на шаблонах из
https://github.com/DalShooth/EVE_PI_Templates):

    {
      "CmdCtrLv": 5,              # уровень Command Center Upgrades, под который собран шаблон
      "Cmt": "Miner - 00 - Bacteria",
      "Diam": 8160.0,             # размер планеты, под которую сохранён шаблон
      "Pln": 11,                  # type_id типа планеты
      "P": [                      # пины (структуры)
        {"H": 10, "La": .., "Lo": .., "S": 2073, "T": 3068},
        ...
      ],
      "L": [{"D": 9, "Lv": 0, "S": 8}, ...],   # линки: S -> D
      "R": [{"P": [8, 6], "Q": 3000, "T": 2073}, ...]  # маршруты
    }

Поля пина:
    H  — число голов экстрактора (0 у не-экстракторов)
    La/Lo — широта/долгота размещения
    S  — schematic_id (что производит структура); null у storage/launchpad
    T  — type_id структуры. ВАЖНО: один и тот же тип структуры имеет
         РАЗНЫЕ type_id на разных типах планет, поэтому категория
         определяется по таблице pin_type_ids в data/pi_reference.json,
         а не по одному значению.

Индексация пинов в "L" и "R" — с ЕДИНИЦЫ, а не с нуля.

Зачем это нужно приложению (а не только характеристики CPU/PG):
  - подставить "CmdCtrLv" под реальный уровень персонажа и отдать готовый
    файл для импорта в игру (README источника прямо описывает эту правку
    как ручную операцию — её можно автоматизировать);
  - пересчитать состав застройки, если шаблон не влезает на планету;
  - определить, какое сырьё/продукт обслуживает шаблон (по schematic_id).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DEFAULT_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "data" / "templates"
DEFAULT_REFERENCE_PATH = Path(__file__).resolve().parent.parent / "data" / "pi_reference.json"


class TemplateParseError(ValueError):
    """Шаблон не удалось разобрать — структура файла не соответствует ожидаемой."""


class NotATemplate(TemplateParseError):
    """
    Файл вообще не является шаблоном колонии.

    Отличается от TemplateParseError намеренно. Шаблон с незнакомой
    структурой — это пробел в наших данных, и загрузка обязана падать
    громко. А посторонний файл в папке — это просто посторонний файл,
    и он не должен мешать разобрать остальные 68.
    """


# Имена файлов в поле source_file, которые загрузчик пропустил как
# не-шаблоны. Заполняется при каждом вызове load_all_templates().
_SKIPPED: list[str] = []


def skipped_files() -> list[str]:
    """Что было пропущено при последней загрузке — для диагностики."""
    return list(_SKIPPED)


@dataclass(frozen=True)
class Pin:
    index: int          # 1-based, как в полях L и R
    type_id: int
    category: str       # "extractor_control_unit" | "basic_industry_facility" | ...
    schematic_id: int | None
    heads: int
    latitude: float
    longitude: float


@dataclass(frozen=True)
class Link:
    source: int         # 1-based индекс пина
    destination: int
    level: int


@dataclass(frozen=True)
class Route:
    path: tuple[int, ...]   # 1-based индексы пинов
    quantity: int
    type_id: int


@dataclass(frozen=True)
class ParsedTemplate:
    """Полностью разобранный шаблон застройки."""

    name: str
    command_center_level: int
    planet_size: float          # поле "Diam"
    planet_type_id: int         # поле "Pln"
    pins: tuple[Pin, ...]
    links: tuple[Link, ...]
    routes: tuple[Route, ...]
    source_file: str | None = None
    raw: dict = field(default_factory=dict, repr=False, compare=False)

    def structure_counts(self) -> dict[str, int]:
        """Сколько структур каждой категории в шаблоне."""
        counts: dict[str, int] = {}
        for pin in self.pins:
            counts[pin.category] = counts.get(pin.category, 0) + 1
        return counts

    @property
    def link_count(self) -> int:
        return len(self.links)

    @property
    def extractor_head_count(self) -> int:
        return sum(pin.heads for pin in self.pins)

    def schematic_ids(self) -> set[int]:
        """Схемы, задействованные в шаблоне (что он производит)."""
        return {pin.schematic_id for pin in self.pins if pin.schematic_id is not None}

    def with_command_center_level(self, level: int) -> dict:
        """
        Копия исходного JSON с изменённым "CmdCtrLv".

        Это ровно та правка, которую README источника предлагает делать
        вручную ("edit the value CmdCtrLv: 5 in the json to the CCU value
        of your toon"). Возвращается словарь, готовый к json.dump —
        файл можно отдать пользователю для импорта в игру.
        """
        if not 0 <= level <= 5:
            raise ValueError(f"Уровень Command Center Upgrades вне диапазона 0-5: {level}")
        updated = json.loads(json.dumps(self.raw))  # глубокая копия
        updated["CmdCtrLv"] = level
        return updated


@lru_cache(maxsize=1)
def _category_by_type_id(reference_path: Path = DEFAULT_REFERENCE_PATH) -> dict[int, str]:
    """Обратный индекс type_id -> категория структуры."""
    reference = json.loads(Path(reference_path).read_text(encoding="utf-8"))
    mapping: dict[int, str] = {}
    for category, type_ids in reference["pin_type_ids"].items():
        if category.startswith("_"):
            continue
        for type_id in type_ids:
            mapping[int(type_id)] = category
    return mapping


def parse_template(raw: dict, source_file: str | None = None) -> ParsedTemplate:
    """
    Разобрать словарь шаблона.

    Неизвестный type_id пина -> TemplateParseError, а не тихая подстановка
    "прочее": незнакомая структура означает, что таблица pin_type_ids
    неполна, и расчёт CPU/PG по такому шаблону будет занижен.
    """
    missing = [f for f in ("CmdCtrLv", "P", "L") if f not in raw]
    if missing:
        # Ни одного из ключевых полей — перед нами не шаблон колонии,
        # а какой-то другой json, случайно оказавшийся в папке.
        if len(missing) == 3:
            raise NotATemplate(
                f"Файл не похож на шаблон колонии: нет полей {', '.join(missing)}"
            )
        raise TemplateParseError(
            f"В шаблоне отсутствуют обязательные поля: {', '.join(missing)}"
        )

    categories = _category_by_type_id()
    pins: list[Pin] = []
    for i, p in enumerate(raw["P"], start=1):
        type_id = int(p["T"])
        category = categories.get(type_id)
        if category is None:
            raise TemplateParseError(
                f"Неизвестный type_id структуры {type_id} (пин {i}). "
                f"Дополните pin_type_ids в data/pi_reference.json."
            )
        pins.append(
            Pin(
                index=i,
                type_id=type_id,
                category=category,
                schematic_id=None if p.get("S") is None else int(p["S"]),
                heads=int(p.get("H") or 0),
                latitude=float(p.get("La", 0.0)),
                longitude=float(p.get("Lo", 0.0)),
            )
        )

    links = tuple(
        Link(source=int(l["S"]), destination=int(l["D"]), level=int(l.get("Lv", 0)))
        for l in raw["L"]
    )
    routes = tuple(
        Route(path=tuple(int(x) for x in r["P"]), quantity=int(r["Q"]), type_id=int(r["T"]))
        for r in raw.get("R", [])
    )

    # Проверка ссылочной целостности: индексы в L и R должны существовать.
    valid = set(range(1, len(pins) + 1))
    for link in links:
        if link.source not in valid or link.destination not in valid:
            raise TemplateParseError(f"Линк ссылается на несуществующий пин: {link}")
    for route in routes:
        unknown = set(route.path) - valid
        if unknown:
            raise TemplateParseError(f"Маршрут ссылается на несуществующие пины {unknown}")

    return ParsedTemplate(
        name=str(raw.get("Cmt", source_file or "unnamed")),
        command_center_level=int(raw["CmdCtrLv"]),
        planet_size=float(raw.get("Diam", 0.0)),
        planet_type_id=int(raw.get("Pln", 0)),
        pins=tuple(pins),
        links=links,
        routes=routes,
        source_file=source_file,
        raw=raw,
    )


def load_template_file(path: Path) -> ParsedTemplate:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return parse_template(raw, source_file=Path(path).name)


def load_all_templates(templates_dir: Path = DEFAULT_TEMPLATES_DIR) -> dict[str, ParsedTemplate]:
    """
    Разобрать все шаблоны из data/templates/.

    По решению проекта используются только варианты 00 — файлы с " - LS - "
    в имени пропускаются.
    """
    directory = Path(templates_dir)
    _SKIPPED.clear()
    if not directory.is_dir():
        return {}

    result: dict[str, ParsedTemplate] = {}
    for path in sorted(directory.glob("*.json")):
        if " - LS - " in path.name:
            continue
        try:
            result[path.stem] = load_template_file(path)
        except NotATemplate:
            # Посторонний файл: пропускаем и запоминаем. Ронять загрузку
            # всех шаблонов из-за одного чужого файла нельзя — именно так
            # miner_p1.json из первой версии обрушивал весь разбор.
            _SKIPPED.append(path.name)
    return result
