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
wiki, support.eveonline.com), не одна ставка «на всякий вывоз». Ставка
вводится пользователем по ТИПУ планеты (Barren, Temperate и т.д.), не
по конкретной планете (в data/planet_industry.csv есть точные
POCO Tax Rate/Owner на планету, но это статичный снимок, который
устаревает молча — правило 1).

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

from domain.throughput import Schematic, load_schematics

HOURS_PER_MONTH = 720.0


def _factory_count(structures_detail: list[dict]) -> int:
    return sum(
        s.get("count", 0) for s in structures_detail
        if str(s.get("kind", "")).endswith("industry_facility")
    )


@dataclass
class RowFlow:
    """Материальный поток одной строки плана — для одного расчёта на строку."""

    planet_type: str
    output_product: str
    output_per_hour: float
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
            "monthly_net_profit": self.monthly_net_profit,
            "missing_prices": sorted(self.missing_prices),
            "missing_rates": sorted(self.missing_rates),
        }


def evaluate_plan_profitability(
    rows: list[dict],
    target_products: list[str],
    prices: dict[str, float],
    poco_rates: dict[str, float],
    schematics: dict[str, Schematic] | None = None,
) -> PlanProfitability:
    """
    rows — строки построенного плана (формат PlanRow.to_dict()): нужны
    res_out, role_key, planet_type, structures_detail.
    poco_rates — {тип планеты: ставка в долях (0.10 = 10%)}.
    prices — {продукт: ISK за единицу}, из снимка рыночных цен.

    Честные пробелы, а не выдумка: продукт без цены — в missing_prices,
    его вклад в выручку/налог не считается (не подставляется 0, просто
    не участвует ни в одной сумме, чтобы не занижать итог молча).
    Планета без введённой пользователем ставки — в missing_rates, её
    вклад в налог тоже пропускается по той же причине.
    """
    schematics = load_schematics() if schematics is None else schematics
    target_set = set(target_products)

    revenue = 0.0
    export_tax = 0.0
    import_tax = 0.0
    missing_prices: set[str] = set()
    missing_rates: set[str] = set()
    any_revenue = False
    any_export = False
    any_import = False

    for row in rows:
        flow = _row_flow(row, schematics)
        if flow is None:
            continue

        rate = poco_rates.get(flow.planet_type)
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
        monthly_net_profit=(
            revenue - export_tax - import_tax if (any_revenue and complete) else None
        ),
        missing_prices=missing_prices,
        missing_rates=missing_rates,
    )
