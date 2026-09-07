"""
Расчёт загрузки CPU и Powergrid планетарных структур.

Контекст. В v1 значения pg_load/cpu_load были захардкожены (95/85/75)
независимо от фактического набора структур — расхождение с readme.md,
обещавшим точный расчёт. При этом в репозитории лежала папка templates/
с JSON-шаблонами застройки (например, miner_p1.json), которые не читал
ни main.py, ни web/index.html — данные были мёртвыми.

Формат шаблона, как он есть сейчас (data/templates/miner_p1.json):

    {"name": "Miner + 10 Basic Factories", "total_cpu": 12400, "total_pg": 16800}

ОГРАНИЧЕНИЯ этого формата — их надо снять до реального расчёта:

  1. Нет разбивки по структурам. Есть только суммы, поэтому нельзя
     ответить на вопрос "а если убрать одну фабрику — влезет?".
  2. Нет привязки к Command Center: неизвестно, какой лимит CPU/PG
     доступен. Без лимита проценты загрузки посчитать невозможно —
     нужна таблица лимитов по типу CC и уровню Command Center Upgrades.
  3. Не указано, включены ли в суммы линки и сам Command Center.
  4. РАСХОЖДЕНИЕ С КОДОМ v1: шаблон называется "10 Basic Factories",
     а main.py считал math.ceil(factories/12) и писал в план
     "Экстрактор + 12 Basic Factories". Одно из двух неверно —
     требует решения до реализации (см. TODO ниже).

Поэтому модуль загружает шаблоны как есть, но расчёт процентов
загрузки остаётся нереализованным, пока не появятся лимиты CC
из проверенного источника (официальная документация / SDE).
Выдумывать эти числа нельзя — на них завязан весь планировщик.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "data" / "templates"

# Количество Basic Industry Facility на добывающей планете.
# TODO(Фаза 1, ТРЕБУЕТ РЕШЕНИЯ): шаблон miner_p1.json говорит 10,
# main.py v1 использовал 12. Зафиксировать одно значение, сверив
# с реальной застройкой в игре, и убрать это расхождение.
BASIC_FACTORIES_PER_MINER_PLANET: int | None = None


@dataclass(frozen=True)
class BuildTemplate:
    """Шаблон застройки планеты, загруженный из data/templates/*.json."""

    name: str
    total_cpu: float
    total_pg: float
    source_file: str

    # TODO(Фаза 1): расширить формат разбивкой по структурам, например
    #   "structures": {"Extractor Control Unit": 1, "Basic Industry Facility": 10,
    #                  "Launchpad": 1, "Storage Facility": 1}
    # и полем "command_center": "Barren". Без этого шаблон нельзя
    # ни пересчитать под другой CCU-уровень, ни адаптировать под планету.


@dataclass(frozen=True)
class ColonyLoadResult:
    cpu_used: float
    cpu_total: float
    powergrid_used: float
    powergrid_total: float

    @property
    def cpu_percent(self) -> float:
        return 0.0 if self.cpu_total == 0 else round(100 * self.cpu_used / self.cpu_total, 1)

    @property
    def powergrid_percent(self) -> float:
        return 0.0 if self.powergrid_total == 0 else round(100 * self.powergrid_used / self.powergrid_total, 1)

    @property
    def fits(self) -> bool:
        return self.cpu_used <= self.cpu_total and self.powergrid_used <= self.powergrid_total


@lru_cache(maxsize=1)
def load_templates(templates_dir: Path = DEFAULT_TEMPLATES_DIR) -> dict[str, BuildTemplate]:
    """
    Загрузить все JSON-шаблоны застройки из data/templates/.

    Это первое место в проекте, где данные из templates/ вообще
    используются — в v1 они не читались ниоткуда.
    """
    templates: dict[str, BuildTemplate] = {}
    directory = Path(templates_dir)
    if not directory.is_dir():
        return templates

    for path in sorted(directory.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        key = path.stem
        templates[key] = BuildTemplate(
            name=raw["name"],
            total_cpu=float(raw["total_cpu"]),
            total_pg=float(raw["total_pg"]),
            source_file=path.name,
        )
    return templates


# TODO(Фаза 1, ТРЕБУЕТ ПРОВЕРЕННОГО ИСТОЧНИКА — не заполнять на глаз):
# доступные CPU/PG командного центра по типу планеты и уровню
# Command Center Upgrades (0-5). Без этой таблицы проценты загрузки
# посчитать нельзя — шаблон даёт только потребление, но не лимит.
COMMAND_CENTER_CAPACITY: dict[tuple[str, int], tuple[float, float]] = {}


def calculate_colony_load(
    command_center_type: str,
    command_center_upgrades_level: int,
    template_key: str,
) -> ColonyLoadResult:
    """
    Посчитать загрузку планеты по шаблону застройки.

    Заменяет захардкоженные "pg_load": 95, "cpu_load": 95 из v1.

    Потребление берётся из шаблона (load_templates), лимит —
    из COMMAND_CENTER_CAPACITY. Пока вторая таблица пуста,
    функция не реализована: возвращать проценты, посчитанные
    от выдуманного лимита, хуже, чем не возвращать ничего.
    """
    raise NotImplementedError(
        "TODO(Фаза 1): заполнить COMMAND_CENTER_CAPACITY из проверенного источника"
    )
