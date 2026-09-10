"""
Экспорт готового плана в Excel (.xlsx).

Контракт фронтенда (web/index.html):
  POST /api/export  body: {plan_data: currentPlan, lang: "ru"|"en"}
  -> файл .xlsx с Content-Disposition

Три листа:
  «План» / Plan       — по строке на планету, как в интерфейсе;
  «Сводка» / Summary   — сколько каких командных центров закупать, с формулами;
  «Проверка» / Check   — загрузка CPU/PG по каждой планете, чтобы видеть запас.

Формулы, а не посчитанные в Python числа: лист должен пересчитываться,
если пользователь вручную поправит строку.

НАГРУЗКА: генерация xlsx держит воркер занятым и ест память, поэтому
размер входных данных ограничен явно.

Язык листа задаётся полем lang: заголовки, имена листов и значения ролей
и структур переводятся здесь. Формула COUNTIF ссылается на переведённое
имя листа, иначе сводка не считается.
"""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO

from flask import Blueprint, send_file

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from api.cache import json_error, parse_json_body

bp = Blueprint("export", __name__)

MAX_PLAN_ROWS = 500

HEADER_FILL = PatternFill("solid", start_color="1F3B4D")
HEADER_FONT = Font(name="Arial", bold=True, color="FFFFFF", size=11)
BODY_FONT = Font(name="Arial", size=10)
WARN_FILL = PatternFill("solid", start_color="FFF2CC")

# key, ширина; заголовок берётся из словаря языка по key.
COLUMNS = [
    ("character", 22), ("role", 20), ("constellation", 16), ("system", 14),
    ("planet", 10), ("planet_type", 14), ("planet_radius_km", 12), ("cc_type", 26),
    ("res_in", 22), ("res_out", 22), ("structures", 14), ("template_key", 16),
    ("cpu_percent", 9), ("pg_percent", 9),
]

_L = {
    "ru": {
        "sheet_plan": "План", "sheet_summary": "Сводка", "sheet_check": "Проверка",
        "character": "Персонаж", "role": "Роль", "constellation": "Констелляция",
        "system": "Система", "planet": "Планета", "planet_type": "Тип планеты",
        "planet_radius_km": "Радиус, км", "cc_type": "Командный центр",
        "res_in": "Вход", "res_out": "Выход", "structures": "Структуры",
        "template_key": "Шаблон", "cpu_percent": "CPU, %", "pg_percent": "PG, %",
        "sum_type": "Тип планеты", "sum_count": "Количество планет", "sum_role": "Роль",
        "sum_role_value": "Добыча и переработка", "total": "Всего",
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
        "character": "Character", "role": "Role", "constellation": "Constellation",
        "system": "System", "planet": "Planet", "planet_type": "Planet type",
        "planet_radius_km": "Radius, km", "cc_type": "Command centre",
        "res_in": "Input", "res_out": "Output", "structures": "Structures",
        "template_key": "Template", "cpu_percent": "CPU, %", "pg_percent": "PG, %",
        "sum_type": "Planet type", "sum_count": "Planet count", "sum_role": "Role",
        "sum_role_value": "Extraction and processing", "total": "Total",
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


def _style_header(sheet, headers: list[str], widths: list[int]) -> None:
    for index, (title, width) in enumerate(zip(headers, widths), start=1):
        cell = sheet.cell(row=1, column=index, value=title)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.freeze_panes = "A2"


def _build_workbook(rows: list[dict], lang: str = "ru") -> Workbook:
    tr = _L.get(lang, _L["ru"])
    workbook = Workbook()

    plan = workbook.active
    plan.title = tr["sheet_plan"]
    _style_header(plan, [tr[key] for key, _ in COLUMNS], [w for _, w in COLUMNS])

    for row_index, item in enumerate(rows, start=2):
        for col_index, (key, _) in enumerate(COLUMNS, start=1):
            if key == "role":
                value = _role_value(item, tr)
            elif key == "structures":
                value = _structures_value(item, tr)
            else:
                value = item.get(key)
            cell = plan.cell(row=row_index, column=col_index, value=value)
            cell.font = BODY_FONT
            if key == "planet_radius_km" and isinstance(value, (int, float)):
                cell.number_format = "#,##0"
            if key in ("cpu_percent", "pg_percent") and isinstance(value, (int, float)):
                cell.number_format = "0.0"
                # Подсветка планет, где почти не осталось запаса: именно
                # они сломаются первыми при любой правке застройки.
                if value >= 90:
                    cell.fill = WARN_FILL

    last = len(rows) + 1

    # Лист сводки: сколько командных центров каждого типа закупать.
    summary = workbook.create_sheet(tr["sheet_summary"])
    _style_header(summary, [tr["sum_type"], tr["sum_count"], tr["sum_role"]], [18, 20, 24])

    types_seen: list[str] = []
    for item in rows:
        planet_type = item.get("planet_type") or "?"
        if planet_type not in types_seen:
            types_seen.append(planet_type)

    for row_index, planet_type in enumerate(sorted(types_seen), start=2):
        summary.cell(row=row_index, column=1, value=planet_type).font = BODY_FONT
        # COUNTIF, а не посчитанное в Python число: если пользователь
        # удалит строку на листе «План», сводка пересчитается сама.
        formula = f"=COUNTIF('{tr['sheet_plan']}'!F2:F{last},A{row_index})"
        cell = summary.cell(row=row_index, column=2, value=formula)
        cell.font = BODY_FONT
        summary.cell(row=row_index, column=3, value=tr["sum_role_value"]).font = BODY_FONT

    total_row = len(types_seen) + 2
    summary.cell(row=total_row, column=1, value=tr["total"]).font = Font(name="Arial", bold=True)
    total = summary.cell(row=total_row, column=2, value=f"=SUM(B2:B{total_row - 1})")
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

    return workbook


@bp.post("/export")
def export_plan():
    payload, error = parse_json_body({"plan_data": list})
    if error:
        return json_error(error)

    rows = payload["plan_data"]
    lang = "en" if str(payload.get("lang", "ru")).lower().startswith("en") else "ru"
    if not rows:
        return json_error("Нечего экспортировать: план пуст")
    if len(rows) > MAX_PLAN_ROWS:
        return json_error(f"Слишком большой план: {len(rows)} строк, максимум {MAX_PLAN_ROWS}")
    if not all(isinstance(item, dict) for item in rows):
        return json_error("Каждый элемент plan_data должен быть объектом")

    stream = BytesIO()
    _build_workbook(rows, lang).save(stream)
    stream.seek(0)

    filename = f"pi-plan-{datetime.now(timezone.utc):%Y%m%d-%H%M}.xlsx"
    return send_file(
        stream,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )
