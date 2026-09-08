"""
Экспорт готового плана в Excel (.xlsx).

Контракт фронтенда (web/index.html:745):
  POST /api/export  body: {plan_data: currentPlan}

Логика экспорта в v1 (main.py, export_plan) была рабочей и адекватной —
переносится почти дословно: два листа («PI Plan» и «Shopping List»),
заливка заголовков, автоширина колонок.

НАГРУЗКА: генерация xlsx держит воркер занятым и ест память. Поэтому
размер входных данных ограничен явно — иначе один большой запрос
блокирует обслуживание остальных пользователей.
"""

from __future__ import annotations

from flask import Blueprint

from api.cache import json_error, parse_json_body

bp = Blueprint("export", __name__)

MAX_PLAN_ROWS = 500


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

    raise NotImplementedError(
        "TODO(Фаза 1): перенести export_plan() из main.py v1 (openpyxl, "
        "PatternFill/Font, два листа) и отдать через flask.send_file с "
        "BytesIO и заголовком Content-Disposition."
    )
