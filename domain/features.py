"""
Реестр возможностей приложения.

ЗАЧЕМ ОН НУЖЕН. В проекте есть правило: после любого изменения
функциональности, версии или данных разделы «Справка» и «О проекте»
обязаны обновляться. Написанное правило протухает первым — его забывают.
Поэтому оно сделано исполняемым.

КАК ЭТО РАБОТАЕТ. Добавляя возможность, вы дописываете её сюда.
Тест tests/test_docs.py проверяет, что каждая запись упомянута в справке
на обоих языках, и падает, пока этого нет. То есть забыть про справку
можно, но сборка об этом скажет.

ПОЧЕМУ КЛЮЧЕВЫЕ СЛОВА, А НЕ ТОЧНЫЙ ТЕКСТ. Требовать дословного
совпадения бессмысленно: справку переписывают, формулировки меняются.
Проверяется факт упоминания по нескольким синонимам — этого достаточно,
чтобы поймать «добавили и забыли», и не мешает переписывать текст.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Feature:
    key: str
    title_ru: str
    # Достаточно совпадения ЛЮБОГО слова из набора: справку переписывают,
    # и жёсткая привязка к формулировке ломала бы тест на ровном месте.
    keywords_ru: tuple[str, ...]
    keywords_en: tuple[str, ...]
    since: str          # версия, в которой появилось
    where: str          # в каком разделе справки уместно


FEATURES: tuple[Feature, ...] = (
    Feature(
        "plan_calculation", "Расчёт плана колоний",
        ("дерево рецептов", "разворачивается до сырья"),
        ("recipe tree", "expanded to raw materials"),
        "0.1.0", "help",
    ),
    Feature(
        "shared_components", "Учёт общих компонентов",
        ("общие компоненты", "суммарной потребностью"),
        ("shared components", "summed demand"),
        "0.1.0", "help",
    ),
    Feature(
        "planet_selection", "Подбор планет под переработку",
        ("Barren", "радиуса"),
        ("Barren", "radius"),
        "0.1.0", "help",
    ),
    Feature(
        "region_selection", "Выбор созвездий и региона целиком",
        ("весь регион", "созвездия"),
        ("whole region", "constellations"),
        "0.5.0", "help",
    ),
    Feature(
        "multi_targets", "Несколько целевых продуктов сразу",
        ("несколько сразу", "несколько продуктов"),
        ("several at once", "several products"),
        "0.5.0", "help",
    ),
    Feature(
        "extraction_margin", "Запас на истощение месторождений",
        ("запас на истощение", "истощение"),
        ("depletion", "safety margin"),
        "0.3.0", "help",
    ),
    Feature(
        "dashboard_views", "Три режима вида дашборда",
        ("режим", "вид"),
        ("view", "mode"),
        "0.4.0", "help",
    ),
    Feature(
        "colony_details", "Подробности колонии",
        ("командный центр", "структур"),
        ("command centre", "structures"),
        "0.4.0", "help",
    ),
    Feature(
        "shopping_list", "Список закупки командных центров",
        ("список закупки", "закупк"),
        ("shopping list", "purchase"),
        "0.4.0", "help",
    ),
    Feature(
        "excel_export", "Выгрузка плана в Excel",
        ("Excel", "выгруз"),
        ("Excel", "export"),
        "0.4.0", "help",
    ),
    Feature(
        "saved_plans", "Сохранение и сравнение планов",
        ("сохранённые планы", "сравнить планы", "сравнение планов"),
        ("saved plans", "compare plans"),
        "0.6.0", "help",
    ),
    Feature(
        "profit_ranking", "Оценка выгоды в ISK на колонию-час",
        ("колонию в час", "колония-час", "выгодн"),
        ("colony-hour", "profitable"),
        "0.5.0", "help",
    ),
    Feature(
        "themes_languages", "Переключение темы и языка",
        ("тем", "язык"),
        ("theme", "language"),
        "0.5.0", "help",
    ),
    Feature(
        "no_esi_on_request", "Обращения к игре только по расписанию",
        ("по расписанию", "не обращается"),
        ("scheduled", "never calls"),
        "0.5.0", "about",
    ),
    Feature(
        "verified_sources", "Данные из проверенных источников",
        ("проверенных источник", "сверен"),
        ("verified sources", "cross-checked"),
        "0.2.0", "about",
    ),
    Feature(
        "honest_gaps", "Честные пробелы вместо выдуманных чисел",
        ("нет данных", "не знает"),
        ("no data", "does not know"),
        "0.3.0", "help",
    ),
)


FEATURES = FEATURES + (
    Feature(
        "size_thresholds", "Пороговые радиусы при выборе системы",
        ("предел", "помещается на планеты до"),
        ("limit", "fits planets up to"),
        "0.6.0", "help",
    ),
    Feature(
        "single_template_choice", "Выбор при упоре в размер планет",
        ("по одному шаблону", "вдвое больше"),
        ("single template", "twice as many"),
        "0.6.0", "help",
    ),
)


FEATURES = FEATURES + (
    Feature(
        "colour_coding", "Цветовая кодировка тиров и ролей",
        ("цвет", "плашк"),
        ("colour", "badge"),
        "0.6.0", "help",
    ),
)


FEATURES = FEATURES + (
    Feature(
        "capacity_advice", "Подсказки по вместимости пула персонажей",
        ("не хватает персонажей", "поместится"),
        ("not enough characters", "fit"),
        "0.7.0", "help",
    ),
    Feature(
        "surplus_mining", "Избыточная добыча свободными персонажами",
        ("избыток", "свободные персонажи"),
        ("surplus", "spare characters"),
        "0.7.0", "help",
    ),
)


FEATURES = FEATURES + (
    Feature(
        "logistics_clustering", "Кучкование колоний по системам",
        ("одной системе", "разъезд"),
        ("same system", "travel"),
        "0.7.0", "help",
    ),
)


def by_section(section: str) -> tuple[Feature, ...]:
    return tuple(f for f in FEATURES if f.where == section)
