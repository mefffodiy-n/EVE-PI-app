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

ПЕРЕСЕЧЕНИЕ ЦЕЛЕВЫХ ПРОДУКТОВ (20.09.2026, по прямому запросу
пользователя при добавлении разбивки выручки по продуктам ниже, ИСПРАВЛЕНО
в тот же день — см. ниже). Мультивыбор целевых продуктов — обычная
функция интерфейса (`multi_targets`), и ничто не мешает выбрать
одновременно, например, и "Data Chips" (P3), и "Broadcast Node" (P4,
который её ест) — каждый СВОЕЙ отдельной целевой линией
(`lines_per_target`). Первая версия этой поправки просто ИСКЛЮЧАЛА из
выручки любой целевой продукт, который где-либо в дереве является
сырьём для другого выбранного целевого продукта — но это неверно:
пользователь прямо указал, что если он выбрал Data Chips ОТДЕЛЬНОЙ
целью (не только как часть цепочки Broadcast Node), у него есть
РЕАЛЬНАЯ, отдельно построенная колония-линия для прямой продажи, и её
выручку нельзя просто убирать целиком только потому, что тот же
продукт ГДЕ-ТО ЕЩЁ в плане используется как сырьё.

Правильное решение — не «включён/исключён», а ДОЛЯ: колонии Data
Chips физически одинаковы независимо от того, для какой цели их
считал планировщик (правило "shared_components" — общие компоненты
считаются суммарной потребностью, одним пулом, не по цепочке
отдельно), поэтому у ОБЩЕГО количества построенных колоний продукта
есть ДОЛЯ, соответствующая ЕГО СОБСТВЕННОМУ прямому целевому спросу
(строке "Data Chips" в target_products с её lines_per_target), и
остаток — уходящий на переработку в Broadcast Node. `Demand.
revenue_share[product]` (`domain/throughput.py`) — эта доля, посчитана
там же, где и весь спрос (прямой целевой расход / суммарный расход
продукта в дереве). Выручка и разбивка по продукту домножаются на эту
долю — не исключаются полностью и не считаются полностью, честная
пропорция.

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


def _row_flow(row: dict, schematics: dict[str, Schematic], duty_cycle: float = 1.0) -> RowFlow | None:
    """
    duty_cycle — доля реального времени работы строки против простоя в
    ожидании довозки сырья в причал (20.09.2026, см. domain/logistics.py
    и domain/throughput.py::Demand.duty_cycles) — домножает и выход, и
    вход строки одинаково: фабрика, реально работающая часть времени,
    в ту же долю меньше потребляет и производит, а не только продаёт
    меньше при той же вместимости причала.
    """
    schematic = schematics.get(row.get("res_out"))
    if schematic is None:
        return None
    factories = _factory_count(row.get("structures_detail") or [])
    if not factories:
        return None

    inputs_per_hour = {}
    if row.get("role_key") == "proc":
        for name in schematic.inputs:
            inputs_per_hour[name] = schematic.input_per_hour(name) * factories * duty_cycle

    return RowFlow(
        planet_type=str(row.get("planet_type", "")),
        output_product=row["res_out"],
        output_per_hour=schematic.output_per_hour * factories * duty_cycle,
        system=row.get("system"),
        planet=row.get("planet"),
        inputs_per_hour=inputs_per_hour,
    )


@dataclass
class PurchaseItem:
    """
    Одна строка списка закупки P1 (19.09.2026, по прямому запросу
    пользователя: «в список покупок должно быть добавлено сырьё на
    месяц с кол-вом по виду ресурса и ценой на момент построения
    плана»). `price`/`monthly_cost` — `None`, когда цены этого продукта
    нет в снимке (честный пробел, не 0 — правило 1); `monthly_qty`
    известно всегда — она приходит из самого плана, не из цен.
    """

    product: str
    monthly_qty: float
    price: float | None
    monthly_cost: float | None

    def to_dict(self) -> dict:
        return {
            "product": self.product,
            "monthly_qty": self.monthly_qty,
            "price": self.price,
            "monthly_cost": self.monthly_cost,
        }


@dataclass
class ProductRevenue:
    """
    Одна строка разбивки выручки по конечным продуктам (20.09.2026, по
    прямому запросу пользователя — увидеть, сколько единиц каждого
    вида получится к концу месяца и сколько это стоит по Jita buy).
    `price`/`monthly_revenue` — `None`, когда цены нет в снимке
    (честный пробел, не 0 — правило 1); `monthly_units` известно
    всегда, оно приходит из самого плана, не из цен.
    """

    product: str
    monthly_units: float
    price: float | None
    monthly_revenue: float | None

    def to_dict(self) -> dict:
        return {
            "product": self.product,
            "monthly_units": self.monthly_units,
            "price": self.price,
            "monthly_revenue": self.monthly_revenue,
        }


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
    # Список закупки постатейно (19.09.2026) — та же сумма, что и
    # monthly_purchase_cost, но по каждому продукту отдельно, для
    # панели «Список закупки сырья» и листа экспорта. Пусто в обычном
    # режиме плана (purchased_p1 не передан) — не отдельное поле «нет
    # закупки», просто пустой список.
    purchase_items: list[PurchaseItem] = field(default_factory=list)
    # Продукты, для которых нет данных об объёме единицы (20.09.2026,
    # см. domain/logistics.py) — их duty cycle честно посчитан как 1.0
    # (без штрафа), а не выдуман; проброс из Demand.missing_volumes,
    # этот модуль их не считает сам.
    missing_volumes: list[str] = field(default_factory=list)
    # Разбивка выручки по конечным продуктам (20.09.2026) — уже с
    # поправкой на revenue_share (см. докстринг модуля,
    # "ПЕРЕСЕЧЕНИЕ ЦЕЛЕВЫХ ПРОДУКТОВ"), сумма monthly_revenue по этому
    # списку равна self.monthly_revenue.
    revenue_by_product: list[ProductRevenue] = field(default_factory=list)
    # Целевые продукты, часть спроса которых уходит на переработку в
    # ДРУГОЙ выбранный целевой продукт этого же плана (revenue_share <
    # 1.0) — {product: доля, посчитанная как выручка} для примечания в
    # панели, не для исключения.
    shared_targets: dict[str, float] = field(default_factory=dict)

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
            "purchase_items": [item.to_dict() for item in self.purchase_items],
            "missing_volumes": sorted(self.missing_volumes),
            "revenue_by_product": [
                item.to_dict() for item in sorted(self.revenue_by_product, key=lambda i: i.product)
            ],
            "shared_targets": [
                {"product": product, "share": share}
                for product, share in sorted(self.shared_targets.items())
            ],
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
    duty_cycles: dict[str, float] | None = None,
    missing_volumes: list[str] | None = None,
    revenue_share: dict[str, float] | None = None,
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

    duty_cycles — {продукт: доля реального времени работы} из
    PlanResult.to_dict()["duty_cycles"] (20.09.2026, режим planner.py
    build_plan(), см. domain/logistics.py и domain/throughput.py::
    Demand.duty_cycles) — пропускная способность причала между тирами:
    фабрика физически простаивает, ожидая новую партию сырья, довезённую
    игроком, и это домножает и выход, и вход каждой строки ДО расчёта
    выручки/налогов (не отдельная поправка поверх готовой суммы —
    иначе легко было бы посчитать её дважды или забыть про налог на
    ввоз). Пустой/не передан — как и раньше, полная загрузка без
    поправки (обратная совместимость для мест, которые ещё не считают
    duty cycle, например настоящие колонии).

    revenue_share — {продукт: доля собственного спроса, идущая на
    прямую продажу, а не на переработку в ДРУГОЙ выбранный целевой
    продукт этого же плана} из PlanResult.to_dict()["revenue_share"]
    (20.09.2026, см. domain/throughput.py::Demand.revenue_share и
    докстринг модуля, "ПЕРЕСЕЧЕНИЕ ЦЕЛЕВЫХ ПРОДУКТОВ") — домножает
    выручку и разбивку по продукту (НЕ налоги — они взимаются со ВСЕГО
    физического перемещения независимо от того, продан ли материал
    напрямую или ушёл дальше по цепочке). Пусто/не передан — 1.0 для
    всех продуктов (обратная совместимость, как и с duty_cycles).
    """
    schematics = load_schematics() if schematics is None else schematics
    target_set = set(target_products)
    duty_cycles = duty_cycles or {}
    revenue_share = revenue_share or {}
    shared_targets = {p: s for p, s in revenue_share.items() if p in target_set and s < 1.0}
    revenue_by_product: dict[str, ProductRevenue] = {}

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
        duty_cycle = duty_cycles.get(row.get("res_out"), 1.0)
        flow = _row_flow(row, schematics, duty_cycle=duty_cycle)
        if flow is None:
            continue

        rate = _resolve_rate(flow.system, flow.planet, planets)
        if rate is None:
            missing_rates.add(flow.planet_type)

        share = revenue_share.get(flow.output_product, 1.0)

        price = prices.get(flow.output_product)
        if price is None:
            missing_prices.add(flow.output_product)
            if flow.output_product in target_set:
                monthly_units = flow.output_per_hour * HOURS_PER_MONTH * share
                entry = revenue_by_product.setdefault(
                    flow.output_product,
                    ProductRevenue(product=flow.output_product, monthly_units=0.0, price=None, monthly_revenue=None),
                )
                entry.monthly_units += monthly_units
        else:
            monthly_output = flow.output_per_hour * HOURS_PER_MONTH
            if flow.output_product in target_set:
                sellable = monthly_output * share
                revenue += sellable * price
                any_revenue = True
                entry = revenue_by_product.setdefault(
                    flow.output_product,
                    ProductRevenue(product=flow.output_product, monthly_units=0.0, price=price, monthly_revenue=0.0),
                )
                entry.monthly_units += sellable
                entry.monthly_revenue = (entry.monthly_revenue or 0.0) + sellable * price
            if rate is not None:
                # Налог — со ВСЕГО физического вывоза, независимо от
                # revenue_share: материал покидает планету целиком, а
                # не только его "продаваемая" доля (см. докстринг модуля).
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

    purchase_items: list[PurchaseItem] = []
    for name, rate_per_hour in (purchased_p1 or {}).items():
        monthly_qty = rate_per_hour * HOURS_PER_MONTH
        price = prices.get(name)
        if price is None:
            missing_prices.add(name)
            purchase_items.append(
                PurchaseItem(product=name, monthly_qty=monthly_qty, price=None, monthly_cost=None)
            )
            continue
        monthly_cost = monthly_qty * price
        purchase_cost += monthly_cost
        any_purchase = True
        purchase_items.append(
            PurchaseItem(product=name, monthly_qty=monthly_qty, price=price, monthly_cost=monthly_cost)
        )

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
        purchase_items=sorted(purchase_items, key=lambda item: item.product),
        missing_volumes=list(missing_volumes or []),
        revenue_by_product=list(revenue_by_product.values()),
        shared_targets=shared_targets,
    )
