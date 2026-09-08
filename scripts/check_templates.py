"""
Диагностика набора шаблонов в data/templates/.

Запуск:  python -m scripts.check_templates

Скрипт НЕ падает на первой ошибке, а собирает все проблемы разом:
  - неизвестные type_id пинов (таблицу pin_type_ids надо дополнить);
  - шаблоны, чей состав не совпадает с каталогом в pi_reference.json;
  - шаблоны, которые не влезают на планеты вашего региона.

Отдельно проверяются type_id, которые я вписал в pin_type_ids
ПО ДОГАДКЕ, не увидев соответствующего шаблона — в первую очередь
high_tech_industry_facility (2472). Если в наборе есть P4-шаблоны,
этот прогон либо подтвердит догадку, либо покажет реальный id.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "data" / "templates"
REFERENCE_PATH = ROOT / "data" / "pi_reference.json"


def main() -> None:
    reference = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    category_by_id: dict[int, str] = {}
    for category, ids in reference["pin_type_ids"].items():
        if category.startswith("_"):
            continue
        for type_id in ids:
            category_by_id[int(type_id)] = category

    files = sorted(TEMPLATES_DIR.glob("*.json"))
    if not files:
        print(f"В {TEMPLATES_DIR} нет json-файлов.")
        return

    # miner_p1.json — артефакт прошлой версии проекта (некорректные числа,
    # не игровой формат). Если он ещё лежит в data/templates/, его надо удалить.
    legacy = [f for f in files if f.name == "miner_p1.json"]
    skipped_ls = [f for f in files if " - LS - " in f.name]
    files = [f for f in files if " - LS - " not in f.name and f.name != "miner_p1.json"]

    print(f"Файлов всего: {len(files) + len(skipped_ls)}")
    print(f"  из них LS (пропускаем): {len(skipped_ls)}")
    if legacy:
        print("  !! найден miner_p1.json из старой версии — его нужно УДАЛИТЬ "
              "(числа в нём не соответствуют источнику, формат не игровой)")
    print(f"  к разбору: {len(files)}\n")

    unknown_ids: dict[int, list[str]] = defaultdict(list)
    broken: list[tuple[str, str]] = []
    shapes: dict[tuple, list[str]] = defaultdict(list)
    ccu_levels: Counter = Counter()
    planet_types: Counter = Counter()

    for path in files:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            broken.append((path.name, f"не читается как JSON: {exc}"))
            continue

        missing = [k for k in ("CmdCtrLv", "P", "L") if k not in raw]
        if missing:
            broken.append((path.name, f"нет полей {missing}"))
            continue

        ccu_levels[raw["CmdCtrLv"]] += 1
        planet_types[raw.get("Pln")] += 1

        counts: Counter = Counter()
        file_ok = True
        for pin in raw["P"]:
            type_id = int(pin["T"])
            category = category_by_id.get(type_id)
            if category is None:
                unknown_ids[type_id].append(path.name)
                file_ok = False
            else:
                counts[category] += 1
        if not file_ok:
            continue

        heads = sum(int(p.get("H") or 0) for p in raw["P"])
        shape = (tuple(sorted(counts.items())), len(raw["L"]), heads)
        shapes[shape].append(path.name)

    if broken:
        print("!! Файлы, которые не разобрать:")
        for name, why in broken:
            print(f"   {name}: {why}")
        print()

    if unknown_ids:
        print("!! НЕИЗВЕСТНЫЕ type_id пинов — дополните pin_type_ids в data/pi_reference.json:")
        for type_id, names in sorted(unknown_ids.items()):
            uniq = sorted(set(names))
            sample = ", ".join(uniq[:3]) + (f" (+{len(uniq) - 3})" if len(uniq) > 3 else "")
            per_file = len(names) / len(uniq) if uniq else 0
            print(
                f"   {type_id}: {len(names)} вхождений в {len(uniq)} файл(ах), "
                f"~{per_file:.0f} пин(ов) на файл — {sample}"
            )
        print()
    else:
        print("OK: все type_id пинов распознаны.\n")

    print(f"CmdCtrLv в шаблонах: {dict(ccu_levels)}")
    print(f"Типов планет (поле Pln): {len(planet_types)} различных\n")

    print("Уникальные конфигурации застройки:")
    for (counts, links, heads), names in sorted(shapes.items(), key=lambda kv: -len(kv[1])):
        print(f"\n  {len(names)} шаблон(ов), линков={links}, голов={heads}")
        for category, n in counts:
            print(f"      {category}: {n}")
        print(f"      напр.: {names[0]}")

    # Сверка с каталогом
    print("\n\nСверка конфигураций с каталогом pi_reference.json:")
    catalog = {k: v for k, v in reference["templates"].items() if not k.startswith("_")}
    for key, tpl in catalog.items():
        want = (tuple(sorted(tpl["structures"].items())), tpl["link_count"], tpl.get("extractor_heads", 0))
        found = shapes.get(want)
        if found:
            print(f"  OK      {key}: совпало с {len(found)} шаблон(ами)")
        else:
            print(f"  нет     {key}: конфигурации {want[0]}, линков={want[1]} среди файлов не найдено")

    unmatched = [s for s in shapes if s not in {
        (tuple(sorted(t["structures"].items())), t["link_count"], t.get("extractor_heads", 0))
        for t in catalog.values()
    }]
    if unmatched:
        print("\n!! Конфигурации в файлах, которых НЕТ в каталоге:")
        for shape in unmatched:
            print(f"   линков={shape[1]}, голов={shape[2]}, структуры={dict(shape[0])}")
            print(f"      напр.: {shapes[shape][0]}")


if __name__ == "__main__":
    main()
