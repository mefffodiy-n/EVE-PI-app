"""
Извлечение количеств вход/выход за цикл из маршрутов реальных шаблонов.

ЗАЧЕМ. Чтобы посчитать, сколько фабрик нужно на каждом тире, нужны
количества сырья на цикл и продукции за цикл. В `recipes.json` есть
только входы; выходов там нет. Придумывать их нельзя.

Но эти числа уже лежат в самих шаблонах: в поле "R" (маршруты) EVE
хранит количество на цикл для каждого перемещения. У фабричного шаблона
маршруты делятся на два вида:

    {"P": [11, 4],  "Q": 40, "T": 2396}   # launchpad -> фабрика: ВХОД
    {"P": [4, 11],  "Q": 5,  "T": 2329}   # фабрика -> launchpad: ВЫХОД

Направление определяется по тому, начинается маршрут с фабрики или
заканчивается на ней. Тип продукта берётся из поля "T", а какой продукт
делает фабрика — из поля "S" её пина.

Так все количества извлекаются из проверенного источника, который у вас
уже есть локально, без обращения к ESI и без догадок.

ВАЖНО (обнаружено 11.09.2026 на живых данных). Ключ, под которым этот
модуль складывает схему в data/schematics.json ("S" пина), — это type_id
продукта, а НЕ настоящий ESI schematic_id (для Plasmoids здесь 2389 —
type_id Plasmoids, а у ESI schematic_id той же фабрики — 122, число из
другого пространства). Раньше здесь было обратное утверждение — не
подтвердилось при сверке с реальным пином через ESI. Для сопоставления
с `schematic_id`, который ESI кладёт в пин фабрики (`GET /characters/
{id}/planets/{id}/`), используйте `scripts.sync_colony_status.schematic_info()`
— он идёт к `GET /universe/schematics/{id}/`, а не к этому файлу.

Запуск:
    python -m scripts.extract_schematics            # показать сводку
    python -m scripts.extract_schematics --write    # записать data/schematics.json
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "data" / "templates"
OUTPUT_PATH = ROOT / "data" / "schematics.json"
REFERENCE_PATH = ROOT / "data" / "pi_reference.json"

FACTORY_CATEGORIES = {
    "basic_industry_facility",
    "advanced_industry_facility",
    "high_tech_industry_facility",
}


def _categories() -> dict[int, str]:
    reference = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    mapping: dict[int, str] = {}
    for category, ids in reference["pin_type_ids"].items():
        if category.startswith("_"):
            continue
        for type_id in ids:
            mapping[int(type_id)] = category
    return mapping


# Опечатки в именах файлов шаблонов автора. Исправляются здесь, а не
# в recipes.json: расхождение только в написании, составы совпадают.
# Без этой правки одна опечатка порождает четыре мнимых расхождения —
# само неузнанное имя плюс три продукта, куда оно входит.
PRODUCT_NAME_FIXES = {
    "Chiral Stuctures": "Chiral Structures",
}


def product_name_from_filename(name: str) -> str:
    """
    Имя продукта из имени файла шаблона.

    "Factory - Biocells.json"          -> "Biocells"
    "Factory - Barren - Biocells.json" -> "Biocells"
    "Miner - 00 - Bacteria.json"       -> "Bacteria"

    Берётся последний сегмент, потому что средние сегменты — это тип
    планеты или зона безопасности, а не продукт.
    """
    stem = name[:-5] if name.lower().endswith(".json") else name
    product = stem.split(" - ")[-1].strip()
    return PRODUCT_NAME_FIXES.get(product, product)


def extract(templates_dir: Path = TEMPLATES_DIR) -> dict:
    """
    Собрать по всем шаблонам количества вход/выход на цикл.

    Возвращает {schematic_id: {facility, inputs: {type_id: qty}, output_qty,
                               output_type_id, sources: [файлы]}}.
    """
    categories = _categories()
    result: dict[int, dict] = {}
    conflicts: list[str] = []

    for path in sorted(templates_dir.glob("*.json")):
        if " - LS - " in path.name or path.name == "miner_p1.json":
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if "P" not in raw or "R" not in raw:
            continue

        pins = raw["P"]
        # 1-based индекс -> (категория, schematic_id)
        pin_info: dict[int, tuple[str | None, int | None]] = {}
        for i, pin in enumerate(pins, start=1):
            category = categories.get(int(pin["T"]))
            schematic = None if pin.get("S") is None else int(pin["S"])
            pin_info[i] = (category, schematic)

        factory_pins = {
            i for i, (cat, _) in pin_info.items() if cat in FACTORY_CATEGORIES
        }
        if not factory_pins:
            continue

        inputs: dict[int, dict[int, int]] = defaultdict(dict)
        outputs: dict[int, tuple[int, int]] = {}
        facility: dict[int, str] = {}

        for route in raw["R"]:
            path_ids = [int(x) for x in route["P"]]
            qty = int(route["Q"])
            type_id = int(route["T"])
            if not path_ids:
                continue

            start, end = path_ids[0], path_ids[-1]

            # Маршрут, ЗАКАНЧИВАЮЩИЙСЯ на фабрике — это вход в неё.
            if end in factory_pins:
                cat, schematic = pin_info[end]
                if schematic is not None:
                    inputs[schematic][type_id] = qty
                    facility[schematic] = cat
            # Маршрут, НАЧИНАЮЩИЙСЯ с фабрики — это её выход.
            elif start in factory_pins:
                cat, schematic = pin_info[start]
                if schematic is not None:
                    outputs[schematic] = (type_id, qty)
                    facility[schematic] = cat

        product = product_name_from_filename(path.name)

        for schematic, ins in inputs.items():
            entry = {
                "product": product,
                "facility": facility.get(schematic),
                "inputs": {str(k): v for k, v in sorted(ins.items())},
                "output_type_id": outputs.get(schematic, (None, None))[0],
                "output_qty": outputs.get(schematic, (None, None))[1],
                "sources": [path.name],
            }
            if schematic in result:
                previous = result[schematic]
                same = (
                    previous["inputs"] == entry["inputs"]
                    and previous["output_qty"] == entry["output_qty"]
                )
                if not same:
                    conflicts.append(
                        f"schematic {schematic}: {previous['sources'][0]} даёт "
                        f"{previous['inputs']}->{previous['output_qty']}, "
                        f"{path.name} даёт {entry['inputs']}->{entry['output_qty']}"
                    )
                previous["sources"].append(path.name)
            else:
                result[schematic] = entry

    # Карта «имя продукта -> type_id» строится из выходов схем: имя даёт
    # файл шаблона, type_id — маршрут выхода фабрики. Оба источника
    # проверенные, догадок нет.
    #
    # Побочная польза: эта же карта закрывает отсутствующий
    # data/type_ids.json, который нужен фронтенду для иконок — и закрывает
    # БЕЗ обращения к ESI, чего требуют правила проекта.
    type_ids: dict[str, int] = {}
    for entry in result.values():
        if entry["output_type_id"] is not None and entry["product"]:
            type_ids[entry["product"]] = entry["output_type_id"]

    # Входы переводятся из type_id в имена по той же карте.
    name_by_id = {v: k for k, v in type_ids.items()}
    for entry in result.values():
        entry["inputs_by_name"] = {
            name_by_id.get(int(type_id), f"type_id:{type_id}"): qty
            for type_id, qty in entry["inputs"].items()
        }

    return {
        "schematics": {str(k): v for k, v in sorted(result.items())},
        "type_ids": dict(sorted(type_ids.items())),
        "conflicts": conflicts,
    }


def cross_check_with_recipes(data: dict) -> list[str]:
    """
    Сверить извлечённые схемы с recipes.json.

    Две независимые выборки должны совпасть по составу и количествам
    входов. Расхождение означает ошибку в одном из наборов — и лучше
    узнать об этом здесь, чем получить неверный план.
    """
    recipes_path = ROOT / "data" / "recipes.json"
    if not recipes_path.is_file():
        return ["data/recipes.json не найден — сверка пропущена"]
    recipes = json.loads(recipes_path.read_text(encoding="utf-8"))

    problems: list[str] = []
    covered = set()
    for entry in data["schematics"].values():
        product = entry["product"]
        covered.add(product)
        recipe = recipes.get(product)
        if recipe is None:
            problems.append(f"{product}: есть шаблон, но нет рецепта в recipes.json")
            continue
        if recipe["type"] == "P1":
            continue  # у P1 в recipes.json указан source, а не inputs
        expected = recipe.get("inputs", {})
        actual = entry["inputs_by_name"]
        if expected != actual:
            problems.append(
                f"{product}: recipes.json даёт {expected}, шаблон даёт {actual}"
            )

    for name, recipe in recipes.items():
        if name not in covered and recipe["type"] != "P1":
            problems.append(f"{name}: есть рецепт, но нет шаблона")

    return problems


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="записать data/schematics.json")
    args = parser.parse_args()

    data = extract()
    schematics = data["schematics"]

    by_facility: dict[str, list] = defaultdict(list)
    for schematic_id, entry in schematics.items():
        by_facility[entry["facility"] or "?"].append((schematic_id, entry))

    print(f"Извлечено схем: {len(schematics)}\n")
    for facility, items in sorted(by_facility.items()):
        print(f"{facility}: {len(items)} схем")
        shapes = defaultdict(int)
        for _, entry in items:
            qty_in = sorted(entry["inputs"].values())
            shapes[(tuple(qty_in), entry["output_qty"])] += 1
        for (qty_in, qty_out), count in sorted(shapes.items()):
            print(f"   вход {list(qty_in)} -> выход {qty_out}  ({count} схем)")
        print()

    missing = [k for k, v in schematics.items() if v["output_qty"] is None]
    if missing:
        print(f"!! Без выхода (не найден маршрут из фабрики): {len(missing)} схем")

    print(f"Карта имя -> type_id: {len(data['type_ids'])} продуктов")

    if data["conflicts"]:
        print("\n!! Противоречия между шаблонами:")
        for line in data["conflicts"]:
            print("   ", line)

    problems = cross_check_with_recipes(data)
    conflicts = [p for p in problems if "даёт" in p]
    missing_template = [p for p in problems if "нет шаблона" in p]
    missing_recipe = [p for p in problems if "нет рецепта" in p]

    if conflicts:
        print(f"\n!! ПРОТИВОРЕЧИЯ В СОСТАВЕ ({len(conflicts)}) — требуют решения.")
        print("   Шаблон ссылается на type_id из самой игры, а не на имя, поэтому")
        print("   при расхождении он обычно надёжнее, чем recipes.json.")
        for line in conflicts:
            print("   ", line)

    if missing_recipe:
        print(f"\n~  Есть шаблон, но нет рецепта ({len(missing_recipe)}):")
        for line in missing_recipe:
            print("   ", line)
        print("   Обычно это опечатка в имени файла — добавьте её в PRODUCT_NAME_FIXES.")

    if missing_template:
        print(f"\n~  Есть рецепт, но нет шаблона ({len(missing_template)}):")
        for line in missing_template:
            print("   ", line)
        print("   Либо шаблон не скачан, либо такого продукта в игре нет.")

    if not problems:
        print("\nOK: составы и количества входов совпали с recipes.json")

    if args.write:
        OUTPUT_PATH.write_text(
            json.dumps(
                {
                    "_source": "Извлечено из маршрутов (поле R) шаблонов в data/templates/ "
                               "скриптом scripts/extract_schematics.py. Количества — "
                               "проверенные данные из самих шаблонов, не догадки.",
                    "schematics": schematics,
                    "type_ids": data["type_ids"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nЗаписано: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
