"""
Проверка, что «Справка» и «О проекте» не отстали от приложения.

ПРАВИЛО ПРОЕКТА: после любого изменения функциональности, версии или
данных эти разделы обязаны обновляться. Правило, записанное только
словами, забывается — поэтому оно здесь исполняемое.

Добавили возможность → допишите её в domain/features.py → этот тест
будет падать, пока справка её не упомянет. Забыть можно; пройти мимо
падающего теста — уже нет.

Формулировки не проверяются дословно: справку переписывают, и жёсткая
привязка ломала бы тест на ровном месте. Проверяется факт упоминания
по набору синонимов из реестра.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from domain.features import FEATURES, by_section

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_CANDIDATES = ("web/index.html", "web/app.html")


def _frontend_text() -> str:
    """
    Файл интерфейса со справкой.

    Ищется не первый попавшийся, а тот, где справка есть: во время
    переезда с прежней версии рядом может лежать старый index.html,
    и тест ругался бы на него вместо рабочего файла.
    """
    seen = []
    for name in FRONTEND_CANDIDATES:
        path = ROOT / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        seen.append(name)
        if "const DOC={" in text:
            return text
    if seen:
        pytest.fail(
            "справка не найдена ни в одном файле интерфейса: " + ", ".join(seen)
        )
    pytest.skip("фронтенд не найден")


def _docs_block() -> str:
    """Содержимое объекта DOC — оба раздела на обоих языках."""
    text = _frontend_text()
    start = text.find("const DOC={")
    if start < 0:
        pytest.fail("во фронтенде нет объекта DOC — справка потеряна")
    end = text.find("function openModal(", start)
    return text[start:end]


def _section(name: str, lang: str) -> str:
    """Один раздел справки на одном языке."""
    block = _docs_block()
    anchor = f"{name}:{{"
    start = block.find(anchor)
    assert start > 0, f"в DOC нет раздела {name}"
    # Раздел заканчивается там, где начинается следующий верхнего уровня.
    tail = block[start:]
    other = "about:{" if name == "help" else "}\n};"
    end = tail.find(other, 1)
    section = tail[: end if end > 0 else len(tail)]

    lang_start = section.find(f"{lang}:{{")
    assert lang_start > 0, f"в разделе {name} нет языка {lang}"
    other_lang = "en:{" if lang == "ru" else "}}"
    lang_end = section.find(other_lang, lang_start + 4)
    return section[lang_start : lang_end if lang_end > 0 else len(section)]


class TestCoverage:
    @pytest.mark.parametrize("feature", FEATURES, ids=lambda f: f.key)
    def test_feature_is_mentioned_in_russian(self, feature):
        """
        Каждая возможность из реестра должна быть описана по-русски.
        Если тест упал — не правьте реестр, допишите справку.
        """
        text = _section(feature.where, "ru").lower()
        assert any(word.lower() in text for word in feature.keywords_ru), (
            f"«{feature.title_ru}» (с версии {feature.since}) не упомянута в разделе "
            f"«{feature.where}» по-русски. Ожидалось одно из: "
            f"{', '.join(feature.keywords_ru)}"
        )

    @pytest.mark.parametrize("feature", FEATURES, ids=lambda f: f.key)
    def test_feature_is_mentioned_in_english(self, feature):
        text = _section(feature.where, "en").lower()
        assert any(word.lower() in text for word in feature.keywords_en), (
            f"«{feature.title_ru}» (since {feature.since}) is missing from the "
            f"«{feature.where}» section in English. Expected one of: "
            f"{', '.join(feature.keywords_en)}"
        )


class TestStructure:
    def test_both_languages_have_the_same_sections(self):
        """
        Разное число заголовков означает, что один язык обновили, а второй
        забыли — самая частая ошибка при двуязычии.
        """
        for name in ("help", "about"):
            ru = len(re.findall(r"<h3>", _section(name, "ru")))
            en = len(re.findall(r"<h3>", _section(name, "en")))
            assert ru == en, (
                f"в разделе «{name}» по-русски {ru} заголовков, по-английски {en}: "
                f"один из языков обновлён, второй нет"
            )

    def test_version_is_taken_from_server_not_hardcoded(self):
        """
        Версия в «О проекте» должна подставляться из /api/meta. Вписанная
        руками, она разойдётся с version.py при первом же выпуске.
        """
        about = _docs_block()
        assert 'id="aboutVersion"' in about

        text = _frontend_text()
        assert "meta.version" in text, "версия не берётся из ответа сервера"

    def test_no_stale_version_numbers_in_prose(self):
        """
        В тексте справки не должно быть номеров версий: они устаревают
        молча. Версия выводится в одном месте — из version.py.
        """
        block = _docs_block()
        # Ищем «0.4.0» и подобное в видимом тексте, а не в служебных полях.
        stale = re.findall(r">\s*v?\d+\.\d+\.\d+\s*<", block)
        assert not stale, f"в справке зашиты номера версий: {stale}"


class TestHonesty:
    def test_help_states_what_is_unknown(self):
        """
        Раздел о том, чего программа не знает, — не украшение. Без него
        пользователь примет пустые значения за поломку, а не за честность.
        """
        for lang, words in (("ru", ("нет данных", "не знает")),
                            ("en", ("no data", "does not know"))):
            text = _section("help", lang).lower()
            assert any(w in text for w in words), (
                f"в справке ({lang}) не сказано, каких данных пока нет"
            )

    def test_profit_figures_are_marked_as_upper_bound(self):
        """Числа выгоды не учитывают налог и доставку — об этом надо сказать."""
        assert "верхняя оценка" in _section("help", "ru").lower()
        assert "upper bound" in _section("help", "en").lower()

    def test_about_mentions_trademark_owner(self):
        """Требование к сторонним приложениям EVE — назвать держателя прав."""
        for lang in ("ru", "en"):
            assert "Fenris Creations" in _section("about", lang)

    def test_about_mentions_license_and_source(self):
        """
        Проект открытый (MIT) — «О проекте» должно называть лицензию,
        автора и давать ссылку на исходный код, а не только версию.
        """
        for lang in ("ru", "en"):
            text = _section("about", lang)
            assert "MIT" in text
            assert "github.com/mefffodiy-n" in text
            assert "mefffodiy-n" in text
