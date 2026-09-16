"""
Разовая задача: заранее узнать объём (м³) каждого продукта PI (P0-P4).

ПОЧЕМУ ПОНАДОБИЛОСЬ. `type_volume()` (scripts/sync_colony_status.py)
кэширует объём предмета в data/cache/type_volumes.json, но запрашивает
его только для того, что РЕАЛЬНО лежит в contents конкретного пина на
момент конкретной синхронизации. Если колонию синхронизировали ДО того,
как в причале появился, скажем, конечный продукт цепочки — объём для
него в кэше просто не появляется, пока колонию не пересинхронизируют
ПОСЛЕ того, как продукт там будет. web/index.html (simulateColonyFactories,
17.09.2026) досчитывает содержимое причала вперёд по времени и всегда
включает туда и остаток сырья, и накопленную продукцию — а раз объёма
для продукции в кэше ещё нет, вся сумма used_m3 честно уходит в null, и
интерфейс откатывается на статичный снимок с последней синхронизации
(найдено пользователем на реальной колонии: причал показывал верное
количество продукта в списке, но неизменный процент заполнения).

Список каталога PI конечен и не меняется без обновления игры — 68
продуктов (P1-P4) плюс R0-сырьё, из которых они делаются (P0). Разово
запросив объём для всех них через публичный ESI-эндпоинт (без
авторизации, не расходует лимит ошибок ни одного персонажа), можно
закрыть этот пробел навсегда — новых типов продукции в каталоге PI не
появится без отдельного обновления data/recipes.json.

Источник имён — то же дерево рецептов, что и у остального приложения
(data/recipes.json: имена P1-P4 продуктов и R0-источников P1), type_id —
существующий статический снапшот data/type_ids.json (уже проверен и
используется остальным приложением, см. CLAUDE.md правило 2).

Запуск (разово):
    python -m scripts.backfill_type_volumes
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _all_pi_product_names() -> set[str]:
    from domain.recipes import load_recipes

    names: set[str] = set()
    for recipe in load_recipes():
        names.add(recipe.name)
        if recipe.source:
            names.add(recipe.source)
    return names


def main() -> int:
    from scripts.sync_colony_status import type_volume
    from scripts.esi_client import EsiClient
    from api.blueprints.reference import _type_ids
    from domain.planets import RESOURCE_NAME_ALIASES

    names = _all_pi_product_names()
    type_ids_by_name = _type_ids()

    # recipes.json называет одно и то же сырьё иначе, чем игра/type_ids.json
    # в паре мест (см. domain/planets.py::RESOURCE_NAME_ALIASES,
    # "Microorganisms" vs "Micro-Organisms") — тот же список алиасов,
    # никакого нового сопоставления не выдумываем.
    def resolve(name: str) -> int | None:
        if name in type_ids_by_name:
            return type_ids_by_name[name]
        for alias in RESOURCE_NAME_ALIASES.get(name, []):
            if alias in type_ids_by_name:
                return type_ids_by_name[alias]
        return None

    resolved_by_name = {n: resolve(n) for n in names}
    missing_names = sorted(n for n, tid in resolved_by_name.items() if tid is None)
    if missing_names:
        print(f"Нет type_id в data/type_ids.json для: {', '.join(missing_names)}")

    type_ids = sorted({tid for tid in resolved_by_name.values() if tid is not None})
    print(f"Продуктов в каталоге: {len(names)}, с известным type_id: {len(type_ids)}")

    client = EsiClient()
    resolved = 0
    for type_id in type_ids:
        volume = type_volume(client, type_id)
        if volume is not None:
            resolved += 1
        else:
            print(f"Не удалось узнать объём для type_id {type_id}")

    print(f"Готово: {resolved}/{len(type_ids)} объёмов в data/cache/type_volumes.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
