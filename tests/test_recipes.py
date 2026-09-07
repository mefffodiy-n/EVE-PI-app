"""
Тесты целостности дерева рецептов (data/recipes.json).

В v1 тестов не было, а рассинхроны в данных существовали и молча
проглатывались (см. roadmap.md, раздел 1).
"""

from __future__ import annotations

from collections import defaultdict

from domain.recipes import load_recipes


def test_all_inputs_resolve_to_known_products_or_raw_materials():
    """
    Каждый вход рецепта — либо другой рецепт, либо R0-сырьё.

    Защищает от опечаток вида Polyramids/Polyaramids, из-за которых
    в v1 цепочка молча обрывалась.
    """
    book = load_recipes()
    known = {r.name for r in book}
    raw_sources = {r.source for r in book if r.source}

    unresolved = {
        inp
        for r in book
        for inp in r.inputs
        if inp not in known and inp not in raw_sources
    }
    assert not unresolved, f"Входы без источника: {sorted(unresolved)}"


def test_p1_recipes_have_raw_source():
    book = load_recipes()
    for recipe in book:
        if recipe.tier == "P1":
            assert recipe.source, f"P1 '{recipe.name}' без поля source"


def test_no_duplicate_recipes_by_input_signature():
    """
    Два продукта одного тира с идентичным набором входов — почти наверняка
    ошибка в данных.

    ИЗВЕСТНОЕ РАСХОЖДЕНИЕ: 'Positron Cord' и 'Ukomi Superconductors' в
    recipes.json имеют одинаковые входы (Superconductors 10 +
    Synthetic Oil 10), при этом 'Positron Cord' отсутствовал в списке
    PI_TYPE_NAMES в main.py v1 (то есть не имел type_id и цены).
    Тест намеренно падает, пока расхождение не разобрано —
    удалять запись без вашего решения нельзя.
    """
    book = load_recipes()
    signatures = defaultdict(list)
    for recipe in book:
        if recipe.inputs:
            signatures[(recipe.tier, tuple(sorted(recipe.inputs.items())))].append(recipe.name)

    duplicates = {k: v for k, v in signatures.items() if len(v) > 1}
    assert not duplicates, f"Дубли по составу входов: {[v for v in duplicates.values()]}"
