"""
Загрузка и представление дерева рецептов Planetary Industry (P0 -> P4).

Источник данных: data/recipes.json — переносится из прошлой реализации
(mefffodiy-n/EVE-PI-app) БЕЗ ИЗМЕНЕНИЙ содержимого. Формат:

    {
      "<Название продукта>": {
        "type": "P1" | "P2" | "P3" | "P4",
        "inputs": {"<Название входа>": <кол-во за цикл>, ...},   # для P2-P4
        "source": "<Название сырья R0>"                          # для P1
      },
      ...
    }

Этот модуль НЕ содержит логику планирования (см. domain/planner.py) —
только загрузку, валидацию и удобные структуры доступа к дереву рецептов.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
import json

DEFAULT_RECIPES_PATH = Path(__file__).resolve().parent.parent / "data" / "recipes.json"

# Известные типы продукции PI. Всё, что выходит за эти рамки в data-файле,
# должно приводить к ошибке валидации, а не проглатываться молча.
VALID_TIERS = {"P1", "P2", "P3", "P4"}


@dataclass(frozen=True)
class Recipe:
    """Один узел дерева рецептов."""

    name: str
    tier: str  # "P1" | "P2" | "P3" | "P4"
    inputs: dict[str, int] = field(default_factory=dict)  # пусто для P1
    source: str | None = None  # заполнено только для P1 (сырьё R0)


class RecipeBook:
    """
    Обёртка над словарём рецептов с быстрым доступом и валидацией.

    Используется как синглтон через load_recipes() — файл читается один раз
    за процесс (или явно перечитывается при необходимости, например в тестах).
    """

    def __init__(self, recipes: dict[str, Recipe]):
        self._recipes = recipes

    def get(self, name: str) -> Recipe | None:
        return self._recipes.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._recipes

    def __iter__(self):
        return iter(self._recipes.values())

    def by_tier(self, tier: str) -> list[Recipe]:
        """Все продукты заданного тира (например, все P4 для выбора цели производства)."""
        raise NotImplementedError(
            "TODO(Фаза 1): вернуть список Recipe с recipe.tier == tier"
        )

    def raw_materials_for(self, product_name: str) -> dict[str, int]:
        """
        Рекурсивно развернуть продукт до сырья R0 с суммарными количествами
        на один цикл верхнего продукта.

        Заменяет build_chain()/aggregate_reqs() из старого main.py, но без
        побочной привязки к фабрикам/персонажам — это чистая функция дерева.
        """
        raise NotImplementedError("TODO(Фаза 1): перенести и протестировать логику разворота дерева")


def _validate_raw(raw: dict) -> None:
    """Базовая валидация структуры recipes.json перед созданием Recipe-объектов."""
    for name, entry in raw.items():
        tier = entry.get("type")
        if tier not in VALID_TIERS:
            raise ValueError(f"Рецепт '{name}': неизвестный тип '{tier}'")
        if tier == "P1" and "source" not in entry:
            raise ValueError(f"Рецепт '{name}' (P1) без поля 'source'")
        if tier != "P1" and "inputs" not in entry:
            raise ValueError(f"Рецепт '{name}' ({tier}) без поля 'inputs'")


@lru_cache(maxsize=1)
def load_recipes(path: Path = DEFAULT_RECIPES_PATH) -> RecipeBook:
    """
    Загрузить и провалидировать data/recipes.json.

    lru_cache здесь — сознательное решение: рецепты статичны в рамках процесса,
    перечитывать файл на каждый запрос планировщика не нужно (в отличие от
    STATIC_DATA в старом main.py, который тоже кэшировал, но вручную и без
    валидации содержимого).
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    _validate_raw(raw)

    recipes = {
        name: Recipe(
            name=name,
            tier=entry["type"],
            inputs=entry.get("inputs", {}),
            source=entry.get("source"),
        )
        for name, entry in raw.items()
    }
    return RecipeBook(recipes)
