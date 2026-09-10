"""
Сообщения плана (предупреждения и допущения) — код + параметры, а не
готовая строка.

ЗАЧЕМ. Раньше planner склеивал русский текст прямо в f-строку, и на
английском интерфейс показывал «ДЕФИЦИТ ДОБЫЧИ…» как есть. Теперь
planner складывает `{"code": ..., ...параметры}`, а перевод — здесь,
одним каталогом на оба языка. Так же, как роли колоний (см. role_meta).

Правило проекта 1 остаётся в силе: тексты описывают только то, что
расчёт действительно знает; выдуманных чисел в шаблонах нет.

Русские шаблоны обязаны дословно совпадать с прежними строками —
на них смотрят тесты и хранилище планов.
"""

from __future__ import annotations

# Предупреждения, которые фронтенд показывает красным блоком, а не
# прячет в «замечания к плану».
CRITICAL = {"extraction_none", "extraction_deficit"}

_CATALOG: dict[str, dict[str, str]] = {
    # ── planner ──────────────────────────────────────────────────────
    "no_schematics_file": {
        "ru": "Нет данных о производительности: файл data/schematics.json отсутствует. "
              "Сформируйте его командой `python -m scripts.extract_schematics --write` — "
              "числа будут извлечены из ваших шаблонов, без обращения к ESI.",
        "en": "No throughput data: the file data/schematics.json is missing. "
              "Build it with `python -m scripts.extract_schematics --write` — the "
              "numbers come from your own templates, with no ESI call.",
    },
    "unknown_product": {
        "ru": "Неизвестный продукт: {product}",
        "en": "Unknown product: {product}",
    },
    "no_template_for_product": {
        "ru": "Для продукта {product} нет шаблона в data/templates/ — расчёт по нему невозможен.",
        "en": "No template in data/templates/ for {product} — it cannot be calculated.",
    },
    "no_usable_products": {
        "ru": "Не удалось построить план: нет пригодных целевых продуктов.",
        "en": "Could not build a plan: no usable target products.",
    },
    "chain_broken_no_schematic": {
        "ru": "Нет схемы производства для {product} — цепочка оборвана.",
        "en": "No production schematic for {product} — the chain is broken.",
    },
    "processing_understaffed": {
        "ru": "Переработка {tier}: не хватило свободных персонажей. "
              "Планеты под колонии есть — нужны исполнители.",
        "en": "Processing {tier}: not enough free characters. "
              "Planets for the colonies are there — the workers are missing.",
    },
    "extraction_none": {
        "ru": "КРИТИЧЕСКИЙ ДЕФИЦИТ: нет планет с сырьём «{resource}» "
              "для производства {product} в выбранных констелляциях.",
        "en": "CRITICAL SHORTAGE: no planet carries «{resource}» "
              "for {product} in the chosen constellations.",
    },
    "extraction_deficit": {
        "ru": "ДЕФИЦИТ ДОБЫЧИ: {product} — нужно {needed} планет, "
              "размещено {placed} ({reason}).",
        "en": "EXTRACTION SHORTAGE: {product} — {needed} planets needed, "
              "{placed} placed ({reason}).",
    },
    "all_slots_full": {
        "ru": "Все слоты планет заняты — резерва под расширение нет.",
        "en": "Every planet slot is taken — no headroom to expand.",
    },
    "direct_p2_used": {
        "ru": "{product} делается прямо на добывающих планетах: {placed} колоний "
              "вместо {split} при раздельном пути. По числу колоний прямой путь "
              "обычно не выигрывает. Его смысл в логистике: с планеты уходит "
              "готовый P2, и возить P1 между планетами не нужно вовсе.",
        "en": "{product} is made right on the extraction planets: {placed} colonies "
              "instead of {split} the split path would take. By colony count the "
              "direct path usually does not win. Its point is logistics: finished "
              "P2 leaves the planet, and no P1 is hauled between planets at all.",
    },
    "direct_p2_short": {
        "ru": "{product}: подходящих планет с обоими видами сырья хватило "
              "только на {placed} колоний из {needed}.",
        "en": "{product}: planets carrying both raw materials covered "
              "only {placed} of {needed} colonies.",
    },
    "surplus_mining_added": {
        "ru": "Свободные персонажи заняты добычей сверх потребности: {colonies} колоний. "
              "Сырьё выбрано из вашей же цепочки, по убыванию дефицитности — "
              "копится то, чего не хватает первым.",
        "en": "Spare characters put on extraction beyond demand: {colonies} colonies. "
              "The raw materials are from your own chain, scarcest first — "
              "what runs out first is what builds up.",
    },
    "extractor_stacking": {
        "ru": "{system} {planet}: {count} наших экстрактора на «{resource}» "
              "({characters}). Месторождение общее, поэтому фактическая выработка "
              "каждого будет ниже расчётной. Если это критично — распределите "
              "добычу по другим планетам или заложите запас (extraction_margin).",
        "en": "{system} {planet}: {count} of our extractors on «{resource}» "
              "({characters}). The deposit is shared, so each one's real yield "
              "will be below the estimate. If that matters — spread extraction "
              "across other planets or add margin (extraction_margin).",
    },
    "no_characters": {
        "ru": "Нет персонажей. Заполните их командой "
              "`python -m scripts.seed_dev_characters` (только dev-окружение).",
        "en": "No characters. Seed them with "
              "`python -m scripts.seed_dev_characters` (dev environment only).",
    },
    # ── factory_site ─────────────────────────────────────────────────
    "site_no_planets": {
        "ru": "В системе {system} вообще нет планет — переработку ставить некуда. "
              "Выберите другую домашнюю систему.",
        "en": "System {system} has no planets at all — nowhere to put processing. "
              "Pick another home system.",
    },
    "site_p4_no_preferred": {
        "ru": "В системе {system} нет планет Barren или Temperate. "
              "Переработка P4 на них и только на них — это ограничение игры, "
              "обойти его нельзя. Доступны только: {types}. "
              "Выберите другую домашнюю систему.",
        "en": "System {system} has no Barren or Temperate planets. "
              "P4 processing goes on those and only those — a game rule, "
              "not something to work around. Available: {types}. "
              "Pick another home system.",
    },
    "site_no_preferred": {
        "ru": "В системе {system} нет планет Barren или Temperate. Переработка "
              "{tier} будет размещена на других типах ({types}) — это допустимо, "
              "но такие планеты в среднем крупнее, и линки обойдутся дороже. "
              "Если запас по CPU/PG окажется мал, выберите другую домашнюю систему.",
        "en": "System {system} has no Barren or Temperate planets. {tier} processing "
              "will go on other types ({types}) — allowed, but such planets are "
              "larger on average and links cost more. If CPU/PG headroom turns out "
              "small, pick another home system.",
    },
    "site_no_ccu5": {
        "ru": "Command Center Upgrades {ccu_level}: два шаблона на планету недоступны "
              "(нужен уровень 5). Используется один шаблон на планету — планет "
              "потребуется вдвое больше.",
        "en": "Command Center Upgrades {ccu_level}: two templates per planet are "
              "unavailable (level 5 required). One template per planet is used — "
              "twice as many planets will be needed.",
    },
    "site_double_too_big": {
        "ru": "В системе {system} ни одна планета Barren/Temperate не подходит под "
              "два шаблона {tier}: самая мелкая — {planet_type} радиусом "
              "{smallest_km} км, а предел при Command Center Upgrades "
              "{ccu_level} — примерно {threshold_km} км. "
              "Варианты: выбрать другую домашнюю систему с планетами поменьше "
              "либо ставить по одному шаблону на планету (планет и персонажей "
              "потребуется вдвое больше).",
        "en": "In system {system} no Barren/Temperate planet fits two {tier} "
              "templates: the smallest is {planet_type} at {smallest_km} km, and the "
              "limit at Command Center Upgrades {ccu_level} is about {threshold_km} km. "
              "Options: pick another home system with smaller planets, or place one "
              "template per planet (twice as many planets and characters needed).",
    },
    "site_p4_launchpad_caveat": {
        "ru": "Замечание: порог для двойного P4-шаблона опирается на "
              "неразрешённое расхождение в источнике по стоимости второго "
              "причала (7200 против 5200 CPU). Взято консервативное значение; "
              "если проверка в игре покажет 5200, предел вырастет примерно "
              "с 9 600 до 61 700 км и это предупреждение станет излишним.",
        "en": "Note: the double-P4 threshold rests on an unresolved source conflict "
              "over the second launchpad's cost (7200 vs 5200 CPU). The conservative "
              "value is used; if in-game testing shows 5200, the limit rises from "
              "about 9,600 to 61,700 km and this warning becomes moot.",
    },
    "site_unplaced": {
        "ru": "В системе {system} не удалось разместить {remaining} шаблонов "
              "{tier} из {requested}: на пригодных планетах "
              "({available} шт. Barren/Temperate) шаблон "
              "не помещается по CPU/PG. Планеты слишком крупные.",
        "en": "In system {system}, {remaining} of {requested} {tier} templates could "
              "not be placed: on the suitable planets ({available} Barren/Temperate) "
              "the template does not fit by CPU/PG. The planets are too large.",
    },
    # ── advice (панель вместимости пула) ─────────────────────────────
    "advice_no_targets": {
        "ru": "Не выбрано ни одного целевого продукта.",
        "en": "No target product selected.",
    },
    "advice_no_production_data": {
        "ru": "Для «{product}» нет данных о производстве.",
        "en": "No production data for «{product}».",
    },
    "advice_no_prices": {
        "ru": "Цены не собраны, поэтому предложения не отсортированы по выгоде. "
              "Соберите снимок: python -m scripts.refresh_market_prices",
        "en": "Prices not collected, so suggestions are not sorted by value. "
              "Collect a snapshot: python -m scripts.refresh_market_prices",
    },
    "advice_no_mining_ccu": {
        "ru": "Ни у одного персонажа нет Command Center Upgrades {ccu} — "
              "добывающий шаблон не поместится ни на одну планету.",
        "en": "No character has Command Center Upgrades {ccu} — "
              "the extraction template will not fit any planet.",
    },
    "advice_higher_tier_available": {
        "ru": "Есть цепочки более высокого тира, помещающиеся в остаток.",
        "en": "Higher-tier chains fit within the spare capacity.",
    },
    "advice_nothing_fits": {
        "ru": "В пул не помещается ни одна полная цепочка. Нужны ещё персонажи "
              "или прокачка Interplanetary Consolidation у имеющихся.",
        "en": "No full chain fits the pool. You need more characters "
              "or more Interplanetary Consolidation on the ones you have.",
    },
    # ── допущения ────────────────────────────────────────────────────
    "assume_planets_per_char": {
        "ru": "Число планет на персонажа принято как Interplanetary Consolidation + 1 "
              "(не подтверждено источником)",
        "en": "Planets per character taken as Interplanetary Consolidation + 1 "
              "(not confirmed by the source)",
    },
    "assume_extraction_margin": {
        "ru": "Число добывающих планет увеличено в {margin} раза "
              "как запас на истощение месторождений (задано пользователем, не расчёт)",
        "en": "Extraction planet count multiplied by {margin} "
              "as a deposit-depletion margin (set by the user, not calculated)",
    },
    "assume_cycle_duration": {
        "ru": "Длительность цикла {facility} принята {minutes} мин "
              "(не подтверждено источником)",
        "en": "Cycle time for {facility} taken as {minutes} min "
              "(not confirmed by the source)",
    },
}

_REASON = {
    "skill": {
        "ru": "не хватило свободных персонажей — планет достаточно, "
              "на одной размещаются колонии нескольких",
        "en": "not enough free characters — planets are sufficient, "
              "one planet carries several characters' colonies",
    },
    "fit": {
        "ru": "добывающий шаблон не помещается на найденные планеты",
        "en": "the extraction template does not fit the planets found",
    },
}


def render(entry: dict, lang: str = "ru") -> str:
    """Строка сообщения из {code, ...параметры} на нужном языке."""
    code = entry["code"]
    template = _CATALOG[code].get(lang) or _CATALOG[code]["ru"]
    params = dict(entry)
    if "reason" in params and params["reason"] in _REASON:
        params["reason"] = _REASON[params["reason"]].get(lang) or _REASON[params["reason"]]["ru"]
    try:
        return template.format(**params)
    except KeyError:
        return template
