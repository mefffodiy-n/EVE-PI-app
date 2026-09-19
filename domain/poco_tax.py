"""
Прибыльность построенного плана с учётом налога POCO.

ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ, А НЕ ЧАСТЬ profit.py. profit.py ранжирует
ОБОБЩЁННУЮ экономику «один полный шаблон каждого продукта»
(FACTORIES_PER_TEMPLATE) — не привязан к конкретному построенному плану
и не знает про реальное число колоний/фабрик. Здесь наоборот: вход —
уже построенный план (список строк с настоящими planet_type и
structures_detail), результат — прибыльность именно ЭТОЙ конкретной
застройки, не абстрактного шаблона.

КАК СЧИТАЕТСЯ НАЛОГ (docs/ROADMAP.md, Фаза 9, 16.09.2026). В игре POCO
берёт налог и на экспорт (вывоз с планеты в космос), и на импорт (ввоз
на планету) — подтверждено официальной документацией (EVE University
wiki, support.eveonline.com), не одна ставка «на всякий вывоз».

ОТКУДА СТАВКА (пересмотрено 17.09.2026, по прямому запросу пользователя
после вопроса «зачем ручной ввод, если ставка уже есть в файле»;
ручной ввод убран целиком 17.09.2026 при переходе к
мультирегиональности — с несколькими регионами переопределение «по
типу планеты» перестаёт быть однозначным). Единственный источник —
точная ставка ЭТОЙ КОНКРЕТНОЙ планеты из `data/planet_industry.csv`
(`domain/planets.py::PlanetBook.poco_rate()`), если план/колония знает
свою систему и номер планеты; иначе — честный пробел (`missing_rates`).
Ставка не «живая» — владелец POCO может сменить её в игре в любой
момент без предупреждения, а сама структура — сменить владельца после
войны за суверенитет; снимок файла честно датирован
(`PlanetBook.POCO_SNAPSHOT_DATE`).

Хопы между колониями НЕ восстанавливаются как граф (в отличие от
simulateColonyFactories() для настоящих колоний, где ESI отдаёт
routes) — плану взять их неоткуда, цепочка ещё не построена в игре.
Вместо приближения «хоп = переход между тирами» здесь используется
точный расчёт БЕЗ графа: каждая строка плана сама знает, что она
производит (res_out, structures_detail) и — если это фабрика, а не
экстрактор — что потребляет (schematic.inputs). Экспортный налог этой
строки взимается по ставке ЕЁ СОБСТВЕННОГО типа планеты с ЕЁ
СОБСТВЕННОГО выхода (продукт всегда покидает планету — на следующий
хоп цепочки или сразу на продажу, если это целевой продукт). Импортный
налог строки-переработки взимается по той же ставке с суммарной
стоимости ЕЁ СОБСТВЕННОГО потребления (то, что ей физически привезли).
Просуммировав экспорт и импорт по ВСЕМ строкам плана, получаем ровно
то же самое, что дало бы суммирование по хопам (экспорт источника +
импорт назначения на каждом хопе) — только без необходимости знать,
какая колония кормит какую именно, потому что каждая сторона хопа уже
целиком описана СВОЕЙ строкой плана.

Выручка считается только с продукции строк, чей res_out — один из
ЦЕЛЕВЫХ продуктов запроса: промежуточные тиры производятся ради
следующей стадии цепочки, не продаются сами по себе.

ДОПУЩЕНИЯ (честно, не выдаются за факт — правило 1):
  - «Полная загруженность» — как и весь остальной план: без простоев,
    без дефицита сырья, без истощения месторождений.
  - HOURS_PER_MONTH = 720 (30 дней × 24 ч) — обычная конвенция
    EVE-калькуляторов, не подгоняется под реальный календарь.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from domain.throughput import Schematic, load_schematics

if TYPE_CHECKING:
    from domain.planets import PlanetBook

HOURS_PER_MONTH = 720.0


def _factory_count(structures_detail: list[dict]) -> int:
    return sum(
        s.get("count", 0) for s in structures_detail
        if str(s.get("kind", "")).endswith("industry_facility")
    )


def _resolve_rate(
    system: str | None,
    planet: object,
    planets: "PlanetBook | None",
) -> float | None:
    """
    Ставка POCO для одной строки/колонии — см. докстринг модуля: точная
    ставка этой планеты из файла, иначе честный пробел.
    """
    if planets is not None and system and planet is not None:
        return planets.poco_rate(system, planet)
    return None


@dataclass
class RowFlow:
    """Материальный поток одной строки плана — для одного расчёта на строку."""

    planet_type: str
    output_product: str
    output_per_hour: float
    system: str | None = None
    planet: object = None
    inputs_per_hour: dict[str, float] = field(default_factory=dict)


def _row_flow(row: dict, schematics: dict[str, Schematic]) -> RowFlow | None:
    schematic = schematics.get(row.get("res_out"))
    if schematic is None:
        return None
    factories = _factory_count(row.get("structures_detail") or [])
    if not factories:
        return None

    inputs_per_hour = {}
    if row.get("role_key") == "proc":
        for name in schematic.inputs:
            inputs_per_hour[name] = schematic.input_per_hour(name) * factories

    return RowFlow(
        planet_type=str(row.get("planet_type", "")),
        output_product=row["res_out"],
        output_per_hour=schematic.output_per_hour * factories,
        system=row.get("system"),
        planet=row.get("planet"),
        inputs_per_hour=inputs_per_hour,
    )


@dataclass
class PlanProfitability:
    monthly_revenue: float | None
    monthly_export_tax: float | None
    monthly_import_tax: float | None
    monthly_net_profit: float | None
    missing_prices: list[str]
    missing_rates: list[str]
    # Стоимость P1, закупленного на бирже (purchase_p1, 18.09.2026) —
    # None в обычном режиме плана (свой P1, стоимость закупки не
    # применима), не 0 — это разные вещи (правило 1, честный пробел).
    monthly_purchase_cost: float | None = None

    def to_dict(self) -> dict:
        return {
            "hours_per_month": HOURS_PER_MONTH,
            "monthly_revenue": self.monthly_revenue,
            "monthly_export_tax": self.monthly_export_tax,
            "monthly_import_tax": self.monthly_import_tax,
            "monthly_tax": (
                None if self.monthly_export_tax is None or self.monthly_import_tax is None
                else self.monthly_export_tax + self.monthly_import_tax
            ),
            "monthly_purchase_cost": self.monthly_purchase_cost,
            "monthly_net_profit": self.monthly_net_profit,
            "missing_prices": sorted(self.missing_prices),
            "missing_rates": sorted(self.missing_rates),
        }


@dataclass
class ColonyProfitability:
    """
    Прибыльность НАСТОЯЩИХ синхронизированных колоний («Мои колонии в
    игре»), не расчётного плана — см. docs/ROADMAP.md, Фаза 9,
    16.09.2026, пункт после `evaluate_plan_profitability`.

    Отличия от версии для плана, обе — прямое решение пользователя:
      - скорость выхода КАЖДОЙ колонии — реальная текущая (с поправкой
        на затухание экстрактора и фактическое простаивание/работу
        фабрик), не теоретическая «на полную»; честно `None`, если
        текущее состояние колонии неизвестно — не подставляется ни 0,
        ни номинал;
      - налог — ТОЛЬКО экспортный, поколонийно: то, что физически
        покидает ИМЕННО ЭТУ планету (ставка по ЕЁ типу), без графа
        между колониями — ESI не отдаёт, куда игрок везёт сырьё дальше
        МЕЖДУ РАЗНЫМИ колониями (в отличие от связей между пинами
        ОДНОЙ колонии, которые есть и уже используются в
        `simulateColonyFactories()` на фронтенде).
    """

    monthly_revenue: float | None
    monthly_tax: float | None
    monthly_net_profit: float | None
    missing_prices: list[str]
    missing_rates: list[str]
    missing_output: list[str]

    def to_dict(self) -> dict:
        return {
            "hours_per_month": HOURS_PER_MONTH,
            "monthly_revenue": self.monthly_revenue,
            "monthly_tax": self.monthly_tax,
            "monthly_net_profit": self.monthly_net_profit,
            "missing_prices": sorted(self.missing_prices),
            "missing_rates": sorted(self.missing_rates),
            "missing_output": sorted(self.missing_output),
        }


def evaluate_colonies_profitability(
    colonies: list[dict],
    prices: dict[str, float],
    planets: "PlanetBook | None" = None,
) -> ColonyProfitability:
    """
    colonies — по одной записи на настоящую синхронизированную колонию:
    `label` (для честного перечисления пробелов), `product` (что именно
    покидает планету — P0 экстрактора без своей фабрики или P1/P2/P3
    её собственной фабрики, фронтенд уже решает это в `colonyOutputFlow()`),
    `units_per_hour` (текущая скорость ЭТОГО потока, `None` — состояние
    неизвестно, `0` — колония простаивает, это тоже честный факт, не
    пробел), `planet_type` (только для честного перечисления пробелов
    по типу, когда ставки нет) и `system`/`planet` — для точной ставки
    этой планеты из файла, см. `_resolve_rate()`.

    Колония без известного текущего потока (`units_per_hour is None`
    или `product is None`) — в `missing_output`, не участвует ни в одной
    сумме (не 0, честный пробел). Цена/ставка не найдены — та же логика
    честных пробелов, что и в `evaluate_plan_profitability`.
    """
    revenue = 0.0
    tax = 0.0
    missing_prices: set[str] = set()
    missing_rates: set[str] = set()
    missing_output: list[str] = []
    any_revenue = False
    any_tax = False

    for colony in colonies:
        label = str(colony.get("label") or colony.get("product") or "?")
        units = colony.get("units_per_hour")
        product = colony.get("product")

        if units is None or product is None:
            missing_output.append(label)
            continue

        planet_type = colony.get("planet_type")
        price = prices.get(product)
        rate = _resolve_rate(colony.get("system"), colony.get("planet"), planets)

        if price is None:
            missing_prices.add(product)
        else:
            monthly_output = units * HOURS_PER_MONTH
            revenue += monthly_output * price
            any_revenue = True
            if rate is not None:
                tax += monthly_output * price * rate
                any_tax = True

        if rate is None:
            missing_rates.add(str(planet_type or "?"))

    complete = not missing_prices and not missing_rates and not missing_output
    return ColonyProfitability(
        monthly_revenue=revenue if any_revenue else None,
        monthly_tax=tax if any_tax else None,
        monthly_net_profit=revenue - tax if (any_revenue and complete) else None,
        missing_prices=missing_prices,
        missing_rates=missing_rates,
        missing_output=missing_output,
    )


def evaluate_plan_profitability(
    rows: list[dict],
    target_products: list[str],
    prices: dict[str, float],
    schematics: dict[str, Schematic] | None = None,
    planets: "PlanetBook | None" = None,
    purchased_p1: dict[str, float] | None = None,
) -> PlanProfitability:
    """
    rows — строки построенного плана (формат PlanRow.to_dict()): нужны
    res_out, role_key, planet_type, structures_detail, system, planet.
    prices — {продукт: ISK за единицу}, из снимка рыночных цен.
    planets — для точной ставки POCO конкретной планеты из файла
    (planets.poco_rate()), см. докстринг модуля.
    purchased_p1 — {продукт P1: единиц в час}, из PlanResult.to_dict()
    ["purchased_p1"] (18.09.2026, режим planner.py::PlanRequest.purchase_p1)
    — P1 этого плана не добыт, а закупается; стоимость закупки считается
    по ТОЙ ЖЕ цене, что и выручка/импортный налог (`prices`, buy_max —
    решение пользователя), второй словарь цен не нужен. Пустой/не
    передан — как и раньше, стоимость закупки не участвует вовсе
    (monthly_purchase_cost остаётся None, не 0).

    Импортный налог на ввоз купленного P1 на планету переработки уже
    считается ниже как обычно (по schematic.inputs каждой строки-
    переработки) — ему всё равно, откуда физически взялся материал,
    своя добыча или закупка. purchased_p1 добавляет только саму
    СТОИМОСТЬ ПОКУПКИ, отдельную от налога на её ввоз.

    Честные пробелы, а не выдумка: продукт без цены — в missing_prices,
    его вклад в выручку/налог не считается (не подставляется 0, просто
    не участвует ни в одной сумме, чтобы не занижать итог молча).
    Планета без известной ставки в файле — в missing_rates, её вклад в
    налог тоже пропускается по той же причине.
    """
    schematics = load_schematics() if schematics is None else schematics
    target_set = set(target_products)

    revenue = 0.0
    export_tax = 0.0
    import_tax = 0.0
    purchase_cost = 0.0
    missing_prices: set[str] = set()
    missing_rates: set[str] = set()
    any_revenue = False
    any_export = False
    any_import = False
    any_purchase = False

    for row in rows:
        flow = _row_flow(row, schematics)
        if flow is None:
            continue

        rate = _resolve_rate(flow.system, flow.planet, planets)
        if rate is None:
            missing_rates.add(flow.planet_type)

        price = prices.get(flow.output_product)
        if price is None:
            missing_prices.add(flow.output_product)
        else:
            monthly_output = flow.output_per_hour * HOURS_PER_MONTH
            if flow.output_product in target_set:
                revenue += monthly_output * price
                any_revenue = True
            if rate is not None:
                export_tax += monthly_output * price * rate
                any_export = True

        if rate is not None:
            for name, qty_per_hour in flow.inputs_per_hour.items():
                input_price = prices.get(name)
                if input_price is None:
                    missing_prices.add(name)
                    continue
                import_tax += qty_per_hour * HOURS_PER_MONTH * input_price * rate
                any_import = True

    for name, rate_per_hour in (purchased_p1 or {}).items():
        price = prices.get(name)
        if price is None:
            missing_prices.add(name)
            continue
        purchase_cost += rate_per_hour * HOURS_PER_MONTH * price
        any_purchase = True

    # Итоговая чистая прибыль — только когда картина ПОЛНАЯ (ни одной
    # пропущенной цены/ставки нигде в плане): частичная сумма тут хуже
    # честного пробела — недостающий налог означал бы, что план выглядит
    # прибыльнее, чем есть на самом деле (правило 1). Сами revenue/
    # export_tax/import_tax по отдельности остаются частичными суммами —
    # это уже честно посчитанная часть, не выдумка.
    complete = not missing_prices and not missing_rates
    return PlanProfitability(
        monthly_revenue=revenue if any_revenue else None,
        monthly_export_tax=export_tax if any_export else None,
        monthly_import_tax=import_tax if any_import else None,
        monthly_purchase_cost=purchase_cost if any_purchase else None,
        monthly_net_profit=(
            revenue - export_tax - import_tax - purchase_cost if (any_revenue and complete) else None
        ),
        missing_prices=missing_prices,
        missing_rates=missing_rates,
    )
