"""
Справочные эндпоинты: констелляции, системы, продукты.

Контракт зафиксирован существующим фронтендом (web/index.html):
  - GET  /api/initial-data -> {status, bases[], products[], product_ids{}}
    (index.html:276 — res.bases, res.products, res.product_ids)
  - POST /api/systems      -> {status, systems[]}
    (index.html:300 — body {constellations: [...]})

product_ids используются фронтом для иконок с images.evetech.net
(index.html:325). Это статичные type_id, поэтому источник — data/type_ids.json
(снапшот), а НЕ живой запрос к ESI при старте, как было в v1.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from domain.planets import load_planets
from domain.recipes import load_recipes

router = APIRouter()


class SystemsRequest(BaseModel):
    constellations: list[str]


@router.get("/initial-data")
def get_initial_data():
    """
    Констелляции, продукты для производства (P2-P4) и их type_id.

    TODO(Фаза 1):
      1. bases       = load_planets().constellations()
      2. products    = [r.name for r in load_recipes() if r.tier in ("P2","P3","P4")]
      3. product_ids = чтение data/type_ids.json (статический снапшот).
         В v1 это был словарь PI_TYPE_IDS, наполняемый запросом к ESI
         /universe/ids/ при каждом старте процесса — именно то, что
         запрещено принципом 0.1 в roadmap.md.
    """
    raise NotImplementedError("TODO(Фаза 1)")


@router.post("/systems")
def get_systems(payload: SystemsRequest):
    """Системы в выбранных констелляциях."""
    raise NotImplementedError("TODO(Фаза 1): load_planets().systems_in(payload.constellations)")
