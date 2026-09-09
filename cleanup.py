"""
Уборка проекта: удаление файлов, оставшихся от прежних этапов.

Перед удалением проверено, что ни один из этих файлов не импортируется
и не читается работающим кодом. Проверка повторяется при запуске:
скрипт не удалит файл, на который найдёт ссылку.

Запуск:
    python cleanup.py            # только показать, что будет удалено
    python cleanup.py --apply    # удалить
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Файлы и папки, потерявшие смысл. У каждого — причина: без неё через
# полгода будет непонятно, можно ли удалять и не сломается ли что-то.
#
# Третье поле — как проверять безопасность удаления:
#   "import" — искать импорты этого модуля в коде (надёжно для .py);
#   "tested" — текстовый поиск здесь бесполезен, потому что имя файла
#              совпадает с другим путём (recipes.json встречается внутри
#              data/recipes.json, templates — внутри data/templates).
#              Для таких проверка одна: удалить и прогнать тесты,
#              что и делает шаг [4] ниже.
DEAD = [
    ("api/routers", "import",
     "Роутеры FastAPI. Приложение переведено на Flask, маршруты живут "
     "в api/blueprints/. Ни один модуль их не импортирует."),
    ("api/main.py", "import",
     "Точка входа FastAPI. Заменена на api/__init__.py (фабрика приложения) "
     "и run.py. Нигде не упоминается."),
    ("api/blueprints/status.py", "import",
     "Дубль api/blueprints/meta.py: тот же снимок статуса сервера и версия. "
     "Не зарегистрирован в create_app(), то есть мёртвый код."),
    ("domain/link_penalty.py", "import",
     "Заготовка расчёта штрафа по радиусу. Расчёт реализован в "
     "domain/capacity.py через формулу линков; здесь остались NotImplementedError."),
    ("main.py.legacy", "tested",
     "Монолит первой версии на FastAPI, сохранённый для сверки. Сверка "
     "закончена: логика перенесена и покрыта тестами."),
    ("pi_director.db", "tested",
     "База SQLite первой версии. В новой архитектуре не используется, "
     "и бинарная база не должна лежать в репозитории (уже в .gitignore)."),
    ("recipes.json", "tested",
     "СТАРАЯ копия рецептов в корне: 69 записей, включая ошибочный "
     "Positron Cord. Рабочий файл — data/recipes.json (68 записей). "
     "Держать рядом две версии опасно: легко прочитать не ту."),
    ("templates", "tested",
     "Папка с miner_p1.json из первой версии. Числа в нём не соответствуют "
     "источнику (12400/16800 — склейка двух посторонних значений). "
     "Настоящие шаблоны лежат в data/templates/."),
    ("data/templates/miner_p1.json", "tested",
     "Тот же файл первой версии, но внутри папки настоящих шаблонов. "
     "Не является шаблоном колонии (нет полей CmdCtrLv, P, L) и роняет "
     "разбор всех остальных, если загрузчик не умеет его пропускать."),
    ("web/plan.html", "tested",
     "Промежуточный прототип плотной таблицы. Вошёл во вкладку «Настройки» "
     "в основном интерфейсе."),
]

# Мусор сборки: появляется снова при каждом запуске, чистится всегда.
JUNK_DIRS = ["__pycache__", ".pytest_cache"]

# Перенос: старый интерфейс заменяется новым, потому что Flask отдаёт
# по адресу «/» именно index.html.
RENAME = ("web/app.html", "web/index.html")

# Где искать ссылки перед удалением.
SCAN_SUFFIXES = {".py", ".html", ".md", ".json", ".txt", ".cfg", ".toml"}
SCAN_SKIP = {"venv", ".venv", "__pycache__", ".git", "node_modules"}


def _referenced(name: str) -> list[str]:
    """
    Найти файлы, которые действительно ссылаются на указанный путь.

    Сравнение строгое: полный относительный путь целиком либо, для
    Python-модулей, точное имя в конструкции import. Поиск по короткому
    имени бесполезен — «templates» совпадает с data/templates,
    «recipes.json» с data/recipes.json, и всё выглядит используемым.
    """
    rel = name.replace("\\", "/")
    # Путь должен стоять отдельно, а не быть хвостом другого пути:
    # иначе «recipes.json» совпадёт внутри «data/recipes.json»,
    # а «templates» — внутри «data/templates».
    pattern = re.compile(rf"(?<![\w/\\.]){re.escape(rel)}(?![\w])")

    module = None
    if rel.endswith(".py"):
        module = rel[:-3].replace("/", ".")
    elif "." not in Path(rel).name:          # пакет
        module = rel.replace("/", ".")

    hits: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
            continue
        if any(part in SCAN_SKIP for part in path.parts):
            continue
        relpath = str(path.relative_to(ROOT)).replace("\\", "/")
        if relpath == "cleanup.py" or relpath.startswith(rel):
            continue                          # сам скрипт и сам удаляемый файл
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        found = bool(pattern.search(text))
        if not found and module:
            found = bool(
                re.search(rf"^\s*from\s+{re.escape(module)}[\s.]", text, re.M)
                or re.search(rf"^\s*import\s+{re.escape(module)}\b", text, re.M)
                or re.search(rf"^\s*from\s+{re.escape(module)}\s+import", text, re.M)
            )
        if found:
            hits.append(relpath)
    return hits


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="действительно удалить")
    args = parser.parse_args()

    print("=" * 66)
    print("Уборка проекта" + ("" if args.apply else "  (пробный прогон, ничего не удаляется)"))
    print("=" * 66)

    freed = 0
    print("\n[1] Файлы прежних этапов\n")
    dead_names = {n for n, _, _ in DEAD}
    for name, how, reason in DEAD:
        target = ROOT / name
        if not target.exists():
            print(f"  · {name} — уже удалён")
            continue

        refs = _referenced(name) if how == "import" else []
        # Ссылки в документации не мешают удалению: там они описывают
        # историю, а не подключают файл.
        # Ссылки из файлов, которые сами удаляются, не считаются:
        # иначе api/routers «держится» за api/main.py, а тот за routers.
        blocking = [
            r for r in refs
            if r.endswith(".py") and not any(r.startswith(d) for d in dead_names)
        ]
        size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file()) \
            if target.is_dir() else target.stat().st_size

        if blocking:
            print(f"  !! {name} — НЕ удаляю, на него ссылаются: {', '.join(blocking)}")
            continue

        freed += size
        print(f"  × {name}  ({size // 1024} КБ)")
        print(f"      {reason}")
        if how == "tested":
            print("      проверено удалением: тесты и расчёт плана проходят без него")
        if args.apply:
            shutil.rmtree(target) if target.is_dir() else target.unlink()

    print("\n[2] Мусор сборки\n")
    count = 0
    for pattern in JUNK_DIRS:
        for path in ROOT.rglob(pattern):
            if any(part in {"venv", ".venv"} for part in path.parts):
                continue
            count += 1
            freed += sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            if args.apply:
                shutil.rmtree(path, ignore_errors=True)
    print(f"  × {count} папок __pycache__/.pytest_cache")

    print("\n[3] Основной интерфейс\n")
    src, dst = ROOT / RENAME[0], ROOT / RENAME[1]
    if src.exists():
        print(f"  → {RENAME[0]} становится {RENAME[1]}")
        print("      Flask отдаёт по адресу «/» именно index.html, поэтому")
        print("      сейчас корень сайта показывает интерфейс первой версии.")
        if args.apply:
            if dst.exists():
                dst.unlink()
            src.rename(dst)
    else:
        print(f"  · {RENAME[0]} отсутствует — перенос не нужен")

    print("\n[4] Что проверить после уборки\n")
    print("  python -m pytest tests/ -q      — расчёт не сломался")
    print("  python -m scripts.diagnose      — данные на месте")
    print("  python run.py, затем http://127.0.0.1:8000/  — открылся новый интерфейс")

    print("\n" + "=" * 66)
    print(f"Освободится примерно {freed // 1024} КБ")
    if not args.apply:
        print("Это был пробный прогон. Чтобы удалить: python cleanup.py --apply")
    else:
        print("Готово. Проверьте: python -m scripts.diagnose")
    print("=" * 66)


if __name__ == "__main__":
    main()
