"""
Экспорт готового плана в Excel (.xlsx).

Контракт фронтенда (web/index.html):
  POST /api/export  body: {plan_data: currentPlan}
  -> файл .xlsx с Content-Disposition

Три листа:
  «План»          — по строке на планету, как в интерфейсе;
  «Сводка»        — сколько каких командных центров закупать, с формулами;
  «Проверка»      — загрузка CPU/PG по каждой планете, чтобы видеть запас.

Формулы, а не посчитанные в Python числа: лист должен пересчитываться,
если пользователь вручную поправит строку.

НАГРУЗКА: генерация xlsx держит воркер занятым и ест память, поэтому
размер входных данных ограничен явно.
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

COLUMNS = [
    ("character", "Персонаж", 22),
    ("role", "Роль", 20),
    ("constellation", "Констелляция", 16),
    ("system", "Система", 14),
    ("planet", "Планета", 10),
    ("planet_type", "Тип планеты", 14),
    ("planet_radius_km", "Радиус, км", 12),
    ("cc_type", "Командный центр", 26),
    ("res_in", "Вход", 22),
    ("res_out", "Выход", 22),
    ("structures", "Структуры", 14),
    ("template_key", "Шаблон", 16),
    ("cpu_percent", "CPU, %", 9),
    ("pg_percent", "PG, %", 9),
]


def _style_header(sheet, headers: list[str], widths: list[int]) -> None:
    for index, (title, width) in enumerate(zip(headers, widths), start=1):
        cell = sheet.cell(row=1, column=index, value=title)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.freeze_panes = "A2"


def _build_workbook(rows: list[dict]) -> Workbook:
    workbook = Workbook()

    plan = workbook.active
    plan.title = "План"
    _style_header(plan, [c[1] for c in COLUMNS], [c[2] for c in COLUMNS])

    for row_index, item in enumerate(rows, start=2):
        for col_index, (key, _, _) in enumerate(COLUMNS, start=1):
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
    summary = workbook.create_sheet("Сводка")
    _style_header(summary, ["Тип планеты", "Количество планет", "Роль"], [18, 20, 24])

    types_seen: list[str] = []
    for item in rows:
        planet_type = item.get("planet_type") or "?"
        if planet_type not in types_seen:
            types_seen.append(planet_type)

    for row_index, planet_type in enumerate(sorted(types_seen), start=2):
        summary.cell(row=row_index, column=1, value=planet_type).font = BODY_FONT
        # COUNTIF, а не посчитанное в Python число: если пользователь
        # удалит строку на листе «План», сводка пересчитается сама.
        formula = f'=COUNTIF(План!F2:F{last},A{row_index})'
        cell = summary.cell(row=row_index, column=2, value=formula)
        cell.font = BODY_FONT
        summary.cell(
            row=row_index, column=3,
            value="Добыча и переработка"
        ).font = BODY_FONT

    total_row = len(types_seen) + 2
    summary.cell(row=total_row, column=1, value="Всего").font = Font(name="Arial", bold=True)
    total = summary.cell(row=total_row, column=2, value=f"=SUM(B2:B{total_row - 1})")
    total.font = Font(name="Arial", bold=True)

    note_row = total_row + 2
    summary.cell(
        row=note_row, column=1,
        value="Количества считаются формулами по листу «План» и пересчитываются "
              "при его правке.",
    ).font = Font(name="Arial", italic=True, size=9)
    summary.cell(
        row=note_row + 1, column=1,
        value=f"Выгружено: {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}",
    ).font = Font(name="Arial", italic=True, size=9)

    # Лист проверки: запас по CPU/PG.
    check = workbook.create_sheet("Проверка")
    _style_header(
        check,
        ["Система", "Планета", "Шаблон", "CPU, %", "PG, %", "Запас PG, %"],
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
    if not rows:
        return json_error("Нечего экспортировать: план пуст")
    if len(rows) > MAX_PLAN_ROWS:
        return json_error(f"Слишком большой план: {len(rows)} строк, максимум {MAX_PLAN_ROWS}")
    if not all(isinstance(item, dict) for item in rows):
        return json_error("Каждый элемент plan_data должен быть объектом")

    stream = BytesIO()
    _build_workbook(rows).save(stream)
    stream.seek(0)

    filename = f"pi-plan-{datetime.now(timezone.utc):%Y%m%d-%H%M}.xlsx"
    return send_file(
        stream,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )
