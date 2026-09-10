"""
Целостность фронтенда и единственность версии.

ПОЧЕМУ ЭТИ ПРОВЕРКИ ЕСТЬ. Обе проблемы уже случались в проекте, и обе
прошли незамеченными до жалобы пользователя:

  1. Сломанный CSS. При замене первой строки многострочного правила
     оставался его хвост — объявление вне блока. Парсер принимает такое
     за селектор, уходит в восстановление и ВЫБРАСЫВАЕТ СЛЕДУЮЩЕЕ
     ПРАВИЛО ЦЕЛИКОМ. Ломается не то правило, которое трогали, поэтому
     глазами это не ловится. Один раз так пропали стили кнопок вида,
     другой — целый блок панели.

  2. Разъехавшиеся версии. Номер был вписан в трёх местах и стал разным:
     0.5.0, 0.6.0 и 0.1.0 одновременно.

Тесты дешёвые и запускаются вместе с остальными.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_CANDIDATES = ("web/index.html", "web/app.html")

# Объявление CSS: «свойство: значение». Именно такие строки не должны
# встречаться вне блоков.
DECLARATION = re.compile(r"^[a-z-]+\s*:")


def _frontend() -> tuple[str, str]:
    """Путь и содержимое рабочего файла интерфейса."""
    seen = []
    for name in FRONTEND_CANDIDATES:
        path = ROOT / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        seen.append(name)
        if "<style>" in text and "const DOC={" in text:
            return name, text
    if seen:
        pytest.fail("рабочий файл интерфейса не опознан: " + ", ".join(seen))
    pytest.skip("фронтенд не найден")


def _style_block(text: str) -> str:
    start = text.index("<style>") + len("<style>")
    return text[start : text.index("</style>")]


class TestStylesheet:
    def test_braces_are_balanced(self):
        """
        Лишняя или недостающая скобка рушит всё, что идёт следом,
        и при этом ничего не сообщает — страница просто выглядит иначе.
        """
        name, text = _frontend()
        css = _style_block(text)
        depth = css.count("{") - css.count("}")
        assert depth == 0, (
            f"{name}: баланс фигурных скобок в CSS равен {depth}. "
            f"Лишняя скобка отменяет все правила после себя."
        )

    def test_no_declarations_outside_rules(self):
        """
        Объявление вне блока парсер принимает за селектор и съедает
        вместе с ним следующее правило. Так дважды пропадали рабочие стили.
        """
        name, text = _frontend()
        depth = 0
        orphans: list[tuple[int, str]] = []
        for number, line in enumerate(_style_block(text).splitlines(), 1):
            stripped = line.strip()
            if (
                depth == 0
                and stripped
                and not stripped.startswith(("/*", "*", "@"))
                and DECLARATION.match(stripped)
            ):
                orphans.append((number, stripped[:70]))
            depth += line.count("{") - line.count("}")

        assert not orphans, (
            f"{name}: объявления вне правил ({len(orphans)}). Чаще всего это "
            f"забытый хвост многострочного правила после правки первой строки:\n"
            + "\n".join(f"  строка {n}: {t}" for n, t in orphans[:5])
        )

    def test_no_script_inside_stylesheet(self):
        """
        Однажды 91 строка JavaScript попала внутрь <style>: код вставлялся
        по комментарию-маркеру, а такой комментарий был и в стилях, и в
        скрипте — замена сработала в обоих местах.
        """
        name, text = _frontend()
        css = _style_block(text)
        found = re.findall(r"\b(?:async\s+)?function\s+(\w+)\s*\(", css)
        assert not found, (
            f"{name}: внутри <style> оказался JavaScript: {', '.join(found[:5])}"
        )

    def test_theme_variables_defined_for_both_themes(self):
        """У светлой темы должен быть свой набор переменных, иначе она частичная."""
        name, text = _frontend()
        css = _style_block(text)
        dark = set(re.findall(r"(--[a-z-]+)\s*:", css[: css.index("body.light")]))
        light_block = css[css.index("body.light") :]
        light = set(re.findall(r"(--[a-z-]+)\s*:", light_block[: light_block.index("}")]))
        missing = dark - light
        # --row-h и подобные метрики темы не касаются: сверяем только цвета.
        missing = {v for v in missing if not v.endswith(("-h", "-w", "-size"))}
        assert not missing, (
            f"{name}: в светлой теме не переопределены переменные: {', '.join(sorted(missing))}"
        )

    def test_no_opaque_colour_hex_literals(self):
        """
        Цвет (фон, обводка SVG, рамка, текст) не задаётся непрозрачным
        hex-литералом — только var(--…) или rgba() (полупрозрачные
        наложения работают в обеих темах). Литерал переживает переключение
        темы: в светлой под курсором был чёрный фон, активные кнопки вида —
        чёрные, панель колонии — чёрная, стрелки в кольцах — невидимые.
        Проверка полноты темы это не ловит — она смотрит только --переменные.
        """
        name, text = _frontend()
        css = _style_block(text)
        in_css = re.findall(
            r"(?:background(?:-color)?|stroke|border(?:-[a-z]+)?|color)\s*:"
            r"\s*[^;{}]*?(#[0-9a-fA-F]{3,8})",
            css,
        )
        # Инлайновый style="…background:#…" в JS-шаблонах — тот же промах.
        inline = re.findall(r'style="[^"]*background\s*:\s*(#[0-9a-fA-F]{3,8})', text)
        bad = in_css + inline
        assert not bad, (
            f"{name}: непрозрачный hex-литерал вместо var(--…): {', '.join(sorted(set(bad)))}"
        )


class TestScript:
    def test_no_duplicate_function_definitions(self):
        """
        Повторное определение функции молча заменяет прежнее. Так уже
        дублировался целый блок при вставке по неуникальному маркеру.
        """
        name, text = _frontend()
        script = text[text.rindex("<script>") : text.rindex("</script>")]
        names = re.findall(r"\b(?:async\s+)?function\s+(\w+)\s*\(", script)
        duplicates = sorted({n for n in names if names.count(n) > 1})
        assert not duplicates, f"{name}: функции определены дважды: {', '.join(duplicates)}"

    def test_translation_dict_has_no_duplicate_keys(self):
        """
        Ключ, объявленный в T дважды, молча затирается вторым значением.
        Так `load` был и «Загрузка», и «Открыть» — на дашборде вместо
        подписи «Загрузка» показывалось «Открыть». Ловим до жалобы.
        """
        name, text = _frontend()
        block = text[text.index("const T={") + len("const T={") : text.index("\nconst t=")]
        # Внутри T две ветки: ru:{...} и en:{...}. Разбираем каждую.
        for lang in ("ru", "en"):
            start = block.index(lang + ":{") + len(lang) + 2
            depth, i = 1, start
            while depth:
                if block[i] == "{":
                    depth += 1
                elif block[i] == "}":
                    depth -= 1
                i += 1
            body = block[start : i - 1]
            # Ключи верхнего уровня: имя перед ':' в начале элемента,
            # не внутри строк и не внутри вложенных [...] массивов.
            keys, buf_depth, in_str = [], 0, ""
            token = ""
            for ch in body:
                if in_str:
                    if ch == in_str:
                        in_str = ""
                    continue
                if ch in "'\"":
                    in_str = ch
                    token = ""
                elif ch == "[":
                    buf_depth += 1
                elif ch == "]":
                    buf_depth -= 1
                elif ch == ":" and buf_depth == 0:
                    keys.append(token.strip().split(",")[-1].strip())
                    token = ""
                elif ch == "," and buf_depth == 0:
                    token = ""
                else:
                    token += ch
            dupes = sorted({k for k in keys if k and keys.count(k) > 1})
            assert not dupes, (
                f"{name}: в T.{lang} ключи объявлены дважды: {', '.join(dupes)}. "
                f"Второе значение молча затирает первое."
            )


    def test_no_script_outside_script_tag(self):
        """
        Код, вставленный по неуникальному маркеру, может попасть не только
        в <style>, но и прямо в разметку. Тогда он не выполняется вовсе,
        а внешне страница выглядит целой. Так уже терялся вызов функции.
        """
        name, text = _frontend()
        body = text[text.index("<body>") : text.rindex("<script>")]
        found = re.findall(r"\b(?:async\s+)?function\s+(\w+)\s*\(", body)
        assert not found, (
            f"{name}: определения функций вне <script>: {', '.join(found[:5])}"
        )


class TestVersion:
    def test_version_lives_in_one_place(self):
        """
        Номер версии вписан только в version.py. Всё остальное берёт его
        оттуда: раньше три места разошлись между собой.
        """
        from version import VERSION

        offenders: list[str] = []
        for path in ROOT.rglob("*.py"):
            if any(part in {"venv", ".venv", "tests", "__pycache__"} for part in path.parts):
                continue
            if path.name == "version.py":
                continue
            # В реестре возможностей номер версии — исторические данные
            # («появилось в 0.6.0»), а не дубликат текущей. Такие записи
            # обязаны переживать выпуск новой версии неизменными.
            if path.name == "features.py":
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            # Ищем номер версии в строковых литералах, а не в требованиях
            # к пакетам и не в адресах.
            for match in re.findall(r'"[^"]*?(\d+\.\d+\.\d+)[^"]*?"', text):
                if match == VERSION:
                    offenders.append(f"{path.relative_to(ROOT)}: {match}")

        assert not offenders, (
            "номер версии продублирован вне version.py:\n  " + "\n  ".join(offenders)
        )

    def test_user_agent_is_built_from_version(self):
        """CCP просит представляться — и делать это одинаково из всего приложения."""
        from version import user_agent

        from scripts.esi_client import USER_AGENT as esi_agent
        from scripts.refresh_market_prices import USER_AGENT as market_agent

        assert esi_agent == market_agent == user_agent()
        assert "PI-Director" in user_agent()

    def test_user_agent_carries_contact(self):
        """Без контакта CCP ограничивает жёстче, а связаться с автором не может."""
        from version import user_agent

        agent = user_agent()
        assert "http" in agent or "@" in agent, (
            "в User-Agent нет контакта: укажите ссылку или почту в version.py"
        )
