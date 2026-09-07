"""
Эндпоинт построения производственного плана.

Контракт зафиксирован фронтендом (web/index.html:468):
  POST /api/calculate
  body:  {constellations: [...], factory_sys: "...", target_products: [...]}
  resp:  {status, data: [...], warning, recommendation}

Имя поля factory_sys сохранено как в v1 — переименование сломало бы
существующий index.html без реальной пользы.

Вся расчётная логика — в domain.planner.build_plan(); роутер только
валидирует вход, достаёт персонажей и вызывает planner.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class PlanApiRequest(BaseModel):
    constellations: list[str]
    factory_sys: str
    target_products: list[str]
    include_direct_p2: bool = False  # Фаза 4, фронтом пока не отправляется


@router.post("/calculate")
def calculate_plan(payload: PlanApiRequest):
    """
    Построить план.

    TODO(Фаза 1):
      1. загрузить персонажей из БД (Фаза 1 — данные от
         scripts/seed_dev_characters.py; в Фазе 3 те же поля придут из ESI,
         planner об источнике не знает);
      2. domain.recipes.load_recipes(), domain.planets.load_planets();
      3. domain.planner.build_plan(...);
      4. вернуть в формате, который ожидает renderDashboard() во фронте.

    ВАЖНО (см. roadmap, раздел "фиктивные данные"): каждый элемент data[]
    должен нести РЕАЛЬНОЕ время до истечения цикла экстрактора либо не нести
    его вовсе. Сейчас index.html:475 дорисовывает hours_left через
    Math.random() — это надо убрать на фронте одновременно с тем, как
    бэкенд начнёт отдавать настоящее значение (Фаза 3, sync_colony_status).
    """
    raise NotImplementedError("TODO(Фаза 1): подключить domain.planner.build_plan")
