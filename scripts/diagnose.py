"""
Самодиагностика приложения.

Проверяет по шагам всё, что нужно для работы интерфейса, и говорит
конкретно, что не так. Нужен потому, что фронтенд при ошибке загрузки
справочников просто оставляет списки пустыми — по такому симптому
причину не определить.

Запуск (сервер запускать не нужно, проверка идёт через тестовый клиент):
    python -m scripts.diagnose
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OK = "  OK  "
BAD = "  !!  "
WARN = "  ~   "


def _line(status: str, text: str) -> None:
    print(f"{status}{text}")


def check_data_files() -> bool:
    print("\n[1] Файлы данных")
    ok = True

    for name, required, hint in [
        ("recipes.json", True, "перенесите из старого репозитория"),
        ("pi_reference.json", True, "входит в скелет проекта"),
        ("planet_industry.csv", True, "скопируйте из корня старого репозитория, "
                                      "переименовав без пробела"),
        ("schematics.json", False, "создайте: python -m scripts.extract_schematics --write"),
    ]:
        path = ROOT / "data" / name
        if path.is_file():
            _line(OK, f"data/{name}")
        elif required:
            _line(BAD, f"data/{name} — НЕ НАЙДЕН ({hint})")
            ok = False
        else:
            _line(WARN, f"data/{name} — нет ({hint})")

    templates = list((ROOT / "data" / "templates").glob("*.json")) if (
        ROOT / "data" / "templates"
    ).is_dir() else []
    real = [t for t in templates if t.name != "miner_p1.json"]
    if real:
        _line(OK, f"data/templates/ — {len(real)} шаблонов")
    else:
        _line(BAD, "data/templates/ — пусто, положите туда набор 00-шаблонов")
        ok = False
    if any(t.name == "miner_p1.json" for t in templates):
        _line(WARN, "data/templates/miner_p1.json — удалите, числа в нём неверны")

    return ok


def check_planets() -> bool:
    print("\n[2] Данные по планетам")
    try:
        from domain.planets import RADIUS_COLUMN, load_planets

        book = load_planets()
        df = book.dataframe
    except FileNotFoundError:
        _line(BAD, "planet_industry.csv не найден — см. пункт [1]")
        return False
    except Exception as exc:
        _line(BAD, f"не удалось прочитать CSV: {type(exc).__name__}: {exc}")
        return False

    _line(OK, f"строк: {len(df)}")

    constellations = book.constellations()
    if constellations:
        _line(OK, f"констелляций: {len(constellations)} (первая: {constellations[0]})")
    else:
        _line(BAD, "констелляций НЕТ — выпадающий список будет пуст")
        return False

    import pandas as pd

    if RADIUS_COLUMN not in df.columns:
        _line(BAD, f"нет колонки «{RADIUS_COLUMN}»")
        return False
    if pd.api.types.is_numeric_dtype(df[RADIUS_COLUMN]):
        _line(OK, "радиус — числовой тип")
    else:
        _line(BAD, "радиус НЕ числовой: сравнения размера планет будут неверны")
        return False

    factory_types = df[df["Type"].isin(["Barren", "Temperate"])] if "Type" in df.columns else []
    if len(factory_types):
        _line(OK, f"планет Barren/Temperate под переработку: {len(factory_types)}")
    else:
        _line(BAD, "нет планет Barren/Temperate — переработку ставить некуда")

    return True


def check_planet_resources() -> bool:
    """
    Сверить planet_industry.csv с раскладкой сырья по типам планет.

    Источник раскладки — eve-webtools.com. Если в CSV у планеты указана
    плотность сырья, которого на этом типе планет не бывает, это ошибка
    в данных, и план по ней будет неверным.
    """
    print("\n[3] Раскладка сырья по типам планет")
    reference_path = ROOT / "data" / "eve_webtools_reference.json"
    if not reference_path.is_file():
        _line(WARN, "data/eve_webtools_reference.json нет — сверка пропущена")
        return True

    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    expected = {k: set(v) for k, v in reference["planet_resources"].items() if not k.startswith("_")}
    all_resources = set().union(*expected.values())

    try:
        from domain.planets import load_planets

        df = load_planets().dataframe
    except Exception:
        _line(WARN, "данные по планетам недоступны — сверка пропущена")
        return True

    if "Type" not in df.columns:
        _line(BAD, "в CSV нет колонки Type")
        return False

    import pandas as pd

    # Сопоставляем колонки CSV с известными названиями сырья
    def simplify(text: str) -> str:
        return str(text).lower().replace("-", "").replace(" ", "")

    by_simple = {simplify(r): r for r in all_resources}
    columns = {c: by_simple[simplify(c)] for c in df.columns if simplify(c) in by_simple}

    if not columns:
        _line(BAD, "ни одна колонка CSV не опознана как сырьё P0")
        return False
    _line(OK, f"опознано колонок сырья: {len(columns)} из {len(all_resources)}")

    missing_columns = all_resources - set(columns.values())
    if missing_columns:
        _line(WARN, f"нет колонок для: {', '.join(sorted(missing_columns))}")

    problems = 0
    unknown_types = set()
    for _, row in df.iterrows():
        planet_type = str(row.get("Type", "")).strip()
        if planet_type not in expected:
            if planet_type:
                unknown_types.add(planet_type)
            continue
        for column, resource in columns.items():
            value = pd.to_numeric(row.get(column), errors="coerce")
            if value and value > 0 and resource not in expected[planet_type]:
                if problems < 5:
                    _line(BAD, f"{row.get('System')} {row.get('Planet')} ({planet_type}): "
                               f"указано «{resource}», которого на этом типе не бывает")
                problems += 1

    if unknown_types:
        _line(WARN, f"неизвестные типы планет в CSV: {', '.join(sorted(unknown_types))}")

    if problems:
        _line(BAD, f"всего несоответствий сырья и типа планеты: {problems}")
        return False
    _line(OK, "сырьё соответствует типам планет")
    return True


def check_recipes_and_schematics() -> bool:
    print("\n[4] Рецепты и производительность")
    from domain.recipes import load_recipes
    from domain.throughput import load_schematics

    recipes = list(load_recipes())
    _line(OK, f"рецептов: {len(recipes)}")

    schematics = load_schematics()
    if not schematics:
        _line(
            BAD,
            "схем производства НЕТ — расчёт плана вернёт предупреждение. "
            "Выполните: python -m scripts.extract_schematics --write",
        )
        return False

    _line(OK, f"схем производства: {len(schematics)}")
    without_output = [k for k, v in schematics.items() if not v.output_qty]
    if without_output:
        _line(WARN, f"без выхода: {len(without_output)} схем")
    return True


def check_characters() -> bool:
    print("\n[5] Персонажи")
    from scripts.seed_dev_characters import load_characters

    characters = load_characters()
    if not characters:
        _line(BAD, "персонажей нет (проверьте, что PI_ENV не выставлен в prod)")
        return False
    _line(OK, f"персонажей: {len(characters)}, слотов планет: "
              f"{sum(c.planet_slots for c in characters)}")
    return True


def check_endpoints() -> bool:
    print("\n[6] Эндпоинты API")
    from api import create_app

    # TESTING=False: нужен обычный обработчик ошибок, иначе исключение
    # пробрасывается наружу и диагностика падает сама.
    client = create_app({"TESTING": False}).test_client()
    ok = True

    try:
        response = client.get("/api/initial-data")
    except Exception as exc:
        _line(BAD, f"GET /api/initial-data упал: {type(exc).__name__}: {exc}")
        return False
    if response.status_code != 200:
        _line(BAD, f"GET /api/initial-data -> {response.status_code}")
        return False
    body = response.get_json() or {}
    for problem in body.get("data_problems") or []:
        _line(BAD, problem)
    bases = body.get("bases") or []
    products = body.get("products") or []
    ids = body.get("product_ids") or {}

    _line(OK if bases else BAD, f"констелляций в ответе: {len(bases)}")
    _line(OK if products else BAD, f"продуктов в ответе: {len(products)}")
    ok = ok and bool(bases) and bool(products)
    if not ids:
        _line(WARN, "product_ids пуст — иконки не отобразятся "
                    "(создайте data/type_ids.json или schematics.json)")

    if bases:
        response = client.post("/api/systems", json={"constellations": bases[:3]})
        systems = (response.get_json() or {}).get("systems") or []
        _line(OK if systems else BAD, f"систем для первых констелляций: {len(systems)}")
        ok = ok and bool(systems)

        if systems and products:
            payload = {
                "constellations": bases[:3],
                "factory_sys": systems[0],
                "target_products": products[:1],
            }
            response = client.post("/api/calculate", json=payload)
            body = response.get_json() or {}
            if response.status_code != 200:
                _line(BAD, f"POST /api/calculate -> {response.status_code}: "
                           f"{body.get('message')}")
                ok = False
            else:
                rows = body.get("data") or []
                _line(OK if rows else WARN,
                      f"пробный расчёт «{products[0]}» в {systems[0]}: строк {len(rows)}")
                for warning in (body.get("warnings") or [])[:5]:
                    _line(WARN, warning)

    response = client.get("/api/market/best-product")
    body = response.get_json() or {}
    if body.get("recommended") is None:
        _line(WARN, "оценка выгоды пуста: снапшот рыночных цен ещё не собирается. "
                    "Сборщик появится в Фазе 2 — по правилам проекта запрос "
                    "пользователя не должен инициировать обращения наружу.")

    return ok


def check_frontend() -> bool:
    print("\n[7] Фронтенд")
    index = ROOT / "web" / "index.html"
    if not index.is_file():
        _line(BAD, "web/index.html не найден")
        return False
    text = index.read_text(encoding="utf-8")

    if "SERVED_BY_FLASK" in text:
        _line(OK, "API_BASE определяется автоматически")
    elif "const API_BASE = '/api'" in text:
        _line(OK, "API_BASE относительный")
    else:
        _line(
            BAD,
            "API_BASE задан абсолютным адресом. Это ломает запросы при открытии "
            "по другому имени хоста (127.0.0.1 против localhost) — разные origin, "
            "а CORS отключён. Возьмите web/index.html из поставки.",
        )
        return False

    _line(WARN, "Открывайте приложение по адресу http://127.0.0.1:8000/ (корень), "
                "а не через Live Server и не как файл: иначе /api отдаст HTML "
                "вместо JSON и списки останутся пустыми")

    if "Math.random() * 22" in text or "Math.random()*22" in text:
        _line(WARN, "во фронтенде остался Math.random() для таймеров экстракторов — "
                    "он рисует выдуманное время как настоящее")
    return True


def main() -> None:
    print("=" * 62)
    print("Диагностика PI Director")
    print("=" * 62)

    results = {
        "файлы данных": check_data_files(),
        "планеты": check_planets(),
        "раскладка сырья": check_planet_resources(),
        "рецепты и схемы": check_recipes_and_schematics(),
        "персонажи": check_characters(),
        "эндпоинты": check_endpoints(),
        "фронтенд": check_frontend(),
    }

    print("\n" + "=" * 62)
    failed = [name for name, ok in results.items() if not ok]
    if failed:
        print("Не пройдено: " + ", ".join(failed))
        print("Смотрите строки, отмеченные !! — там указано, что делать.")
    else:
        print("Все проверки пройдены.")
    print("=" * 62)


if __name__ == "__main__":
    main()
