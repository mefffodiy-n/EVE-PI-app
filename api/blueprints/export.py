"""
Экспорт готового плана и/или настоящих колоний в Excel (.xlsx).

Контракт фронтенда (web/index.html):
  POST /api/export  body: {plan_data: currentPlan, colonies_data: coloniesData,
                            lang: "ru"|"en"}
  -> файл .xlsx с Content-Disposition

Оба поля необязательны, но хотя бы одно должно быть непустым списком —
экспортировать нечего, если нет ни плана, ни колоний.

Листы (18.09.2026, добавлены настоящие колонии — по прямому запросу
пользователя: «раз можем экспортировать план, почему не колонии»):
  «План» / Plan         — по строке на планету, как в интерфейсе
                          (только если plan_data непуст);
  «Сводка» / Summary     — сколько каких командных центров закупать, с
                          формулами (только вместе с «План» — у настоящих
                          колоний командные центры уже куплены, список
                          закупки не нужен);
  «Проверка» / Check     — загрузка CPU/PG по каждой планете плана, чтобы
                          видеть запас (та же оговорка, что и у «Сводки»);
  «Мои колонии» / My colonies — настоящие колонии (только если
                          colonies_data непуст), тем же форматом колонок,
                          что и «План» — единственным листом, без своих
                          сводки/проверки (решение пользователя: они не
                          нужны для уже построенного).

Если plan_data пуст, а colonies_data — нет: книга состоит из ОДНОГО
листа «Мои колонии», без «Плана»/«Сводки»/«Проверки» вовсе.

Формулы, а не посчитанные в Python числа: лист «Сводка» должен
пересчитываться, если пользователь вручную поправит лист «План».

НАГРУЗКА: генерация xlsx держит воркер занятым и ест память, поэтому
размер входных данных ограничен явно (оба списка по отдельности).

Язык листа задаётся полем lang: заголовки, имена листов и значения ролей
и структур переводятся здесь. Формула COUNTIF ссылается на переведённое
имя листа, иначе сводка не считается.
"""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO

from flask import Blueprint, request, send_file

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from api.cache import json_error

bp = Blueprint("export", __name__)

MAX_EXPORT_ROWS = 500

HEADER_FILL = PatternFill("solid", start_color="1F3B4D")
HEADER_FONT = Font(name="Arial", bold=True, color="FFFFFF", size=11)
BODY_FONT = Font(name="Arial", size=10)
WARN_FILL = PatternFill("solid", start_color="FFF2CC")

# key, ширина; заголовок берётся из словаря языка по key.
COLUMNS = [
    ("character", 22), ("role", 20), ("constellation", 16), ("system", 14),
    ("planet", 10), ("planet_type", 14), ("planet_radius_km", 12), ("cc_type", 26),
    ("res_in", 22), ("res_out", 22), ("structures", 14), ("template_key", 16),
    ("cpu_percent", 9), ("pg_percent", 9), ("poco_rate", 12),
]

_L = {
    "ru": {
        "sheet_plan": "План", "sheet_summary": "Сводка", "sheet_check": "Проверка",
        "sheet_colonies": "Мои колонии",
        "character": "Персонаж", "role": "Роль", "constellation": "Констелляция",
        "system": "Система", "planet": "Планета", "planet_type": "Тип планеты",
        "planet_radius_km": "Радиус, км", "cc_type": "Командный центр",
        "res_in": "Вход", "res_out": "Выход", "structures": "Структуры",
        "template_key": "Шаблон", "cpu_percent": "CPU, %", "pg_percent": "PG, %",
        "poco_rate": "Налог POCO, %",
        "sum_type": "Тип планеты", "sum_count": "Количество планет",
        "cc_shopping_title": "Список закупки: командные центры",
        "total": "Всего командных центров",
        "note_formula": "Количества считаются формулами по листу «План» и "
                        "пересчитываются при его правке.",
        "exported": "Выгружено: {stamp}",
        "chk_system": "Система", "chk_planet": "Планета", "chk_template": "Шаблон",
        "chk_cpu": "CPU, %", "chk_pg": "PG, %", "chk_reserve": "Запас PG, %",
        "role_mine": "Добыча", "role_mine_surplus": "Добыча (избыток)",
        "role_proc": "Переработка", "role_direct_p2": "Прямое P2",
        "fact": "фабрик",
    },
    "en": {
        "sheet_plan": "Plan", "sheet_summary": "Summary", "sheet_check": "Check",
        "sheet_colonies": "My colonies",
        "character": "Character", "role": "Role", "constellation": "Constellation",
        "system": "System", "planet": "Planet", "planet_type": "Planet type",
        "planet_radius_km": "Radius, km", "cc_type": "Command centre",
        "res_in": "Input", "res_out": "Output", "structures": "Structures",
        "template_key": "Template", "cpu_percent": "CPU, %", "pg_percent": "PG, %",
        "poco_rate": "POCO tax, %",
        "sum_type": "Planet type", "sum_count": "Planet count",
        "cc_shopping_title": "Shopping list: Command Centers",
        "total": "Total Command Centers",
        "note_formula": "Counts are formulas over the Plan sheet and recalculate "
                        "when it is edited.",
        "exported": "Exported: {stamp}",
        "chk_system": "System", "chk_planet": "Planet", "chk_template": "Template",
        "chk_cpu": "CPU, %", "chk_pg": "PG, %", "chk_reserve": "PG headroom, %",
        "role_mine": "Extraction", "role_mine_surplus": "Extraction (surplus)",
        "role_proc": "Processing", "role_direct_p2": "Direct P2",
        "fact": "factories",
    },
}


def _role_value(item: dict, tr: dict) -> str:
    """Роль в языке листа: из role_key/role_tier, с русской строкой как запас."""
    key = item.get("role_key")
    if not key:
        return item.get("role") or ""
    label = tr.get(f"role_{key}", item.get("role") or key)
    tier = item.get("role_tier")
    return f"{label} {tier}" if tier else label


def _structures_value(item: dict, tr: dict) -> str:
    fs = item.get("factory_summary") or {}
    if fs.get("advanced") is not None:
        return f"{fs['advanced']} {tr['fact']} + {fs['basic']} basic"
    if fs.get("factories") is not None:
        return f"{fs['factories']} {tr['fact']}"
    return item.get("structures") or ""


def _style_header(sheet, headers: list[str], widths: list[int], start_row: int = 1) -> None:
    for index, (title, width) in enumerate(zip(headers, widths), start=1):
        cell = sheet.cell(row=start_row, column=index, value=title)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.freeze_panes = f"A{start_row + 1}"


def _write_rows_sheet(sheet, rows: list[dict], tr: dict) -> None:
    """
    Заполнить лист по общему формату COLUMNS — одинаково для «План» и
    «Мои колонии» (18.09.2026): один и тот же набор колонок, один и тот
    же смысл каждой, разница только в источнике строк.
    """
    _style_header(sheet, [tr[key] for key, _ in COLUMNS], [w for _, w in COLUMNS])
    for row_index, item in enumerate(rows, start=2):
        for col_index, (key, _) in enumerate(COLUMNS, start=1):
            if key == "role":
                value = _role_value(item, tr)
            elif key == "structures":
                value = _structures_value(item, tr)
            else:
                value = item.get(key)
            cell = sheet.cell(row=row_index, column=col_index, value=value)
            cell.font = BODY_FONT
            if key == "planet_radius_km" and isinstance(value, (int, float)):
                cell.number_format = "#,##0"
            if key == "poco_rate" and isinstance(value, (int, float)):
                # value — доля (0.03 = 3%), формат Excel сам домножит на
                # 100 для отображения; значение "нет данных" (None) даёт
                # пустую ячейку, а не 0% (правило 1 — честный пробел).
                cell.number_format = "0.0%"
            if key in ("cpu_percent", "pg_percent") and isinstance(value, (int, float)):
                cell.number_format = "0.0"
                # Подсветка планет, где почти не осталось запаса: именно
                # они сломаются первыми при любой правке застройки.
                if value >= 90:
                    cell.fill = WARN_FILL


def _colony_to_row(colony: dict, planets, recipes) -> dict:
    """
    Настоящая колония (одна запись `/api/colonies`) → строка того же
    формата, что и у расчётного плана (COLUMNS) — 18.09.2026, по прямому
    запросу пользователя: «раз можем экспортировать план, почему не
    колонии».

    Текущий продукт фабрики/экстрактора берётся с её пина (`pin.product`)
    ДАЖЕ когда сейчас простаивает — ESI не стирает назначенную схему
    производства, когда истекает цикл, само назначение и таймер это
    разные вещи (тот же факт, на котором построен pinCycleInfo() во
    фронтенде). Радиус, ставка POCO и констелляция — из того же файла
    данных, что и весь остальной расчёт (`PlanetBook.radius_km()`/
    `poco_rate()`/`constellation_of()`); все честно `None`, если планета
    за пределами загруженного региона — не 0/пустая строка без объяснения
    и не подстановка среднего.

    **Роль — по экстрактору, даже если на планете есть и фабрики**
    (18.09.2026, найдено пользователем на реальных данных: колония с
    экстрактором Felsic Magma и 8 фабриками, перерабатывающими её же
    выход в Silicon на месте — обычная оптимизация, не вывозить P1 —
    показывала «Переработка» на КАЖДОЙ такой колонии). Экстрактор решает
    первым — та же приоритетность, что уже в `realRows()`
    (`web/index.html`) для карточек колоний на дашборде; здесь просто
    забыли её повторить, когда добавляли экспорт.

    **Вход фабрики — из `domain.recipes` (`Recipe.inputs`/`.source`),
    не из `domain.throughput.load_schematics()`** (тот же день, тот же
    отчёт пользователя: колонка «Вход» показывала `type_id:2307` вместо
    «Felsic Magma»). `data/schematics.json` резолвит имена входов только
    по `data/type_ids.json`, а там нет сырья R0 (оно не структура и не
    продукт с иконкой) — есть отдельный, уже проверенный по источникам
    (правило 2) способ узнать сырьё P1-рецепта: `Recipe.source`. Тот же
    способ уже используют "recipe_inputs" в `/api/initial-data`
    (`api/blueprints/reference.py::_initial_payload()`).
    """
    system = colony.get("system_name") or ""
    planet_number = colony.get("planet_index")
    # ESI отдаёт тип строчными («barren»), в файле данных и у плана —
    # с заглавной («Barren») — та же нормализация, что и во фронтенде
    # (realRows(), web/index.html).
    raw_type = str(colony.get("planet_type") or "")
    planet_type = raw_type[:1].upper() + raw_type[1:] if raw_type else ""

    pins = colony.get("pins") or []
    extractor_pins = [p for p in pins if p.get("kind") == "extractor_control_unit"]
    factory_pins = [p for p in pins if str(p.get("kind") or "").endswith("industry_facility")]

    role_key = "mine" if extractor_pins else "proc" if factory_pins else None
    res_out = res_in = None
    # {"factories": N} — тот же формат, что и у расчётного плана
    # (PlanRow.factory_summary), не голая русская строка: тогда
    # _structures_value()/_write_rows_sheet() переводят число на язык
    # листа сами, лист на английском не остаётся с русским словом внутри.
    factory_summary: dict = {}
    if role_key == "mine":
        res_out = extractor_pins[0].get("product")
        heads = extractor_pins[0].get("heads")
        factory_summary = {"factories": heads or len(extractor_pins)}
    elif role_key == "proc":
        res_out = factory_pins[0].get("product")
        recipe = recipes.get(res_out) if res_out else None
        if recipe and recipe.inputs:
            res_in = ", ".join(sorted(recipe.inputs))
        elif recipe and recipe.source:
            res_in = recipe.source
        factory_summary = {"factories": len(factory_pins)}

    radius = planets.radius_km(system, planet_number) if planets and planet_number is not None else None
    poco_rate = planets.poco_rate(system, planet_number) if planets and planet_number is not None else None
    constellation = planets.constellation_of(system) if planets and system else None

    return {
        "character": colony.get("character"),
        "role_key": role_key,
        "constellation": constellation,
        "system": system,
        "planet": str(planet_number) if planet_number is not None else "",
        "planet_type": planet_type,
        "planet_radius_km": radius,
        "cc_type": f"1x {planet_type} Command Center" if planet_type else None,
        "res_in": res_in,
        "res_out": res_out,
        "factory_summary": factory_summary,
        "template_key": None,
        "cpu_percent": colony.get("cpu_percent"),
        "pg_percent": colony.get("pg_percent"),
        "poco_rate": poco_rate,
    }


def _load_planets_and_recipes():
    """
    Тот же способ читать справочники, что и у остальных эндпоинтов
    (`api/blueprints/plans.py::_load_planets_book()`) — `None`, если
    файла нет, а не падение: экспорт колоний тогда просто останется без
    радиуса/ставки POCO/констелляции/входа рецепта, честно (правило 1).
    """
    try:
        from domain.planets import load_planets

        planets = load_planets()
    except FileNotFoundError:
        planets = None
    from domain.recipes import load_recipes

    return planets, load_recipes()


def _build_workbook(rows: list[dict], colony_rows: list[dict], lang: str = "ru") -> Workbook:
    tr = _L.get(lang, _L["ru"])
    workbook = Workbook()
    first_sheet_used = False

    if rows:
        plan = workbook.active
        plan.title = tr["sheet_plan"]
        first_sheet_used = True
        _write_rows_sheet(plan, rows, tr)
        _write_summary_and_check_sheets(workbook, rows, tr)

    if colony_rows:
        colonies_sheet = workbook.active if not first_sheet_used else workbook.create_sheet()
        colonies_sheet.title = tr["sheet_colonies"]
        _write_rows_sheet(colonies_sheet, colony_rows, tr)

    return workbook


def _write_summary_and_check_sheets(workbook: Workbook, rows: list[dict], tr: dict) -> None:
    last = len(rows) + 1

    # Лист сводки: сколько командных центров каждого типа закупать —
    # отдельным явно подписанным блоком (17.09.2026, по прямому запросу
    # пользователя), тем же смыслом, что и «Список закупки» в вебе
    # (renderShopping(), web/index.html): раньше колонка с типом планеты
    # неявно подразумевала «это и есть нужный командный центр» — теперь
    # заголовок блока и отдельная колонка называют его прямо, а не
    # оставляют читателю догадываться по количеству и типам планет.
    summary = workbook.create_sheet(tr["sheet_summary"])
    summary.cell(row=1, column=1, value=tr["cc_shopping_title"]).font = Font(
        name="Arial", bold=True, size=12
    )
    _style_header(
        summary, [tr["sum_type"], tr["sum_count"], tr["cc_type"]], [18, 20, 30], start_row=2
    )

    types_seen: list[str] = []
    for item in rows:
        planet_type = item.get("planet_type") or "?"
        if planet_type not in types_seen:
            types_seen.append(planet_type)

    for row_index, planet_type in enumerate(sorted(types_seen), start=3):
        summary.cell(row=row_index, column=1, value=planet_type).font = BODY_FONT
        # COUNTIF, а не посчитанное в Python число: если пользователь
        # удалит строку на листе «План», сводка пересчитается сама.
        formula = f"=COUNTIF('{tr['sheet_plan']}'!F2:F{last},A{row_index})"
        cell = summary.cell(row=row_index, column=2, value=formula)
        cell.font = BODY_FONT
        # Тоже формула, не строка из Python — тем же принципом, что и
        # COUNTIF выше: если пользователь поправит тип планеты в A,
        # название командного центра рядом обновится само.
        cc_formula = f'=A{row_index}&" Command Center"'
        summary.cell(row=row_index, column=3, value=cc_formula).font = BODY_FONT

    total_row = len(types_seen) + 3
    summary.cell(row=total_row, column=1, value=tr["total"]).font = Font(name="Arial", bold=True)
    total = summary.cell(row=total_row, column=2, value=f"=SUM(B3:B{total_row - 1})")
    total.font = Font(name="Arial", bold=True)

    note_row = total_row + 2
    summary.cell(row=note_row, column=1, value=tr["note_formula"]).font = Font(
        name="Arial", italic=True, size=9
    )
    stamp = f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}"
    summary.cell(
        row=note_row + 1, column=1, value=tr["exported"].format(stamp=stamp)
    ).font = Font(name="Arial", italic=True, size=9)

    # Лист проверки: запас по CPU/PG.
    check = workbook.create_sheet(tr["sheet_check"])
    _style_header(
        check,
        [tr["chk_system"], tr["chk_planet"], tr["chk_template"],
         tr["chk_cpu"], tr["chk_pg"], tr["chk_reserve"]],
        [14, 10, 18, 10, 10, 14],
    )
    for row_index, item in enumerate(rows, start=2):
        check.cell(row=row_index, column=1, value=item.get("system")).font = BODY_FONT
        check.cell(row=row_index, column=2, value=item.get("planet")).font = BODY_FONT
        check.cell(row=row_index, column=3, value=item.get("template_key")).font = BODY_FONT
        check.cell(row=row_index, column=4, value=item.get("cpu_percent")).font = BODY_FONT
        check.cell(row=row_index, column=5, value=item.get("pg_percent")).font = BODY_FONT
        reserve = check.cell(row=row_index, column=6, value=f"=100-E{row_index}")
        reserve.font = BODY_FONT
        reserve.number_format = "0.0"


def _validate_rows(rows: object, field_name: str) -> str | None:
    """Общая проверка plan_data/colonies_data — список объектов в пределах лимита."""
    if not isinstance(rows, list):
        return f"Поле '{field_name}' должно быть списком"
    if len(rows) > MAX_EXPORT_ROWS:
        return f"Слишком много строк в '{field_name}': {len(rows)}, максимум {MAX_EXPORT_ROWS}"
    if not all(isinstance(item, dict) for item in rows):
        return f"Каждый элемент '{field_name}' должен быть объектом"
    return None


@bp.post("/export")
def export_plan():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return json_error("Ожидается JSON-объект в теле запроса")

    rows = payload.get("plan_data") or []
    colonies = payload.get("colonies_data") or []
    lang = "en" if str(payload.get("lang", "ru")).lower().startswith("en") else "ru"

    if not rows and not colonies:
        return json_error("Нечего экспортировать: нет ни плана, ни колоний")
    for field_name, value in (("plan_data", rows), ("colonies_data", colonies)):
        error = _validate_rows(value, field_name)
        if error:
            return json_error(error)

    colony_rows = []
    if colonies:
        planets, recipes = _load_planets_and_recipes()
        colony_rows = [_colony_to_row(c, planets, recipes) for c in colonies]

    stream = BytesIO()
    _build_workbook(rows, colony_rows, lang).save(stream)
    stream.seek(0)

    filename = f"pi-plan-{datetime.now(timezone.utc):%Y%m%d-%H%M}.xlsx"
    return send_file(
        stream,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )
