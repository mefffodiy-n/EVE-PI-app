"""
Разовая задача: узнать настоящие type_id структур PI по типам планет.

ПОЧЕМУ ПОНАДОБИЛОСЬ. `data/type_ids.json` хранит по одному «структура:вид»
на каждую нецентровую структуру (launchpad, storage_facility и т.д.) —
этого хватало для иконок на дашборде. Но выяснилось на живых данных
(11.09.2026, реальная колония «Storm»): в игре КАЖДАЯ структура, а не
только командный центр, специфична для типа планеты — «Storm Basic
Industry Facility» и «Temperate Basic Industry Facility» физически разные
предметы с разными type_id. `scripts/sync_colony_status.py` эту разницу
не знал и молча пропускал пины с «незнакомым» type_id — реальные колонии
на нетемпературных/небарренных планетах показывали только командный
центр, хотя пинов было куда больше.

Источник — сама ESI (`POST /universe/ids/`, публичный, батч резолвинг
имя -> id, без авторизации). Единственный способ получить это без
скачивания статических данных игры (SDE) целиком.

Запуск (разово, печатает готовый блок для data/type_ids.json):
    python -m scripts.resolve_pi_structure_type_ids
    python -m scripts.resolve_pi_structure_type_ids --write   # сразу дописать в файл
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TYPE_IDS_PATH = ROOT / "data" / "type_ids.json"

PLANET_TYPES = [
    "Barren", "Gas", "Ice", "Lava", "Oceanic", "Plasma", "Storm", "Temperate",
]

# Отображаемые имена структур в игре — то, что стоит перед именем типа
# планеты (уже проверено на «Storm Basic Industry Facility» и т.п.).
STRUCTURE_NAMES = [
    "Launchpad",
    "Storage Facility",
    "Extractor Control Unit",
    "Basic Industry Facility",
    "Advanced Industry Facility",
    "High-Tech Production Plant",  # не «…Industry Facility» — в игре именно так
]


def main() -> int:
    from scripts.esi_client import EsiClient, EsiError

    write = "--write" in sys.argv

    names = [f"{planet} {struct}" for planet in PLANET_TYPES for struct in STRUCTURE_NAMES]

    client = EsiClient()
    try:
        response = client.post("/universe/ids/", names)
    except EsiError as exc:
        print(f"Не удалось получить id: {exc}")
        return 1

    resolved: dict[str, int] = {}
    for item in (response.data or {}).get("inventory_types", []):
        resolved[item["name"]] = int(item["id"])

    missing = sorted(set(names) - set(resolved))
    if missing:
        print("Не найдены в ESI (проверьте написание):")
        for name in missing:
            print(f"  · {name}")

    ordered = dict(sorted(resolved.items()))
    print(json.dumps(ordered, ensure_ascii=False, indent=2))
    print(f"\nРазрешено {len(resolved)} из {len(names)}.")

    if write:
        current = json.loads(TYPE_IDS_PATH.read_text(encoding="utf-8"))
        current.update(ordered)
        current = dict(sorted(current.items()))
        TYPE_IDS_PATH.write_text(
            json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"Записано в {TYPE_IDS_PATH}")

    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
