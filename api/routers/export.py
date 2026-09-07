"""
Экспорт готового плана в Excel (.xlsx).

Контракт фронтенда (web/index.html:745):
  POST /api/export  body: {plan_data: currentPlan}

Логика экспорта в v1 (main.py, export_plan) была рабочей и адекватной —
переносится почти дословно: два листа ("PI Plan" и "Shopping List"),
заливка заголовков, автоширина колонок.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class ExportRequest(BaseModel):
    plan_data: list[dict]


@router.post("/export")
def export_plan(payload: ExportRequest):
    """
    Сформировать .xlsx с планом и сводкой по типам Command Center.

    TODO(Фаза 1): перенести export_plan() из main.py v1 (openpyxl,
    PatternFill/Font, StreamingResponse с Content-Disposition).
    """
    raise NotImplementedError("TODO(Фаза 1): перенести export_plan() из main.py v1")
