"""
Штрафы на пропускную способность командных линков в зависимости
от радиуса планеты и расстояния между планетами.

Контекст (см. roadmap.md, раздел 1): readme.md прошлой версии заявлял
"applying advanced distance and radius penalties to prevent powergrid
overloads", но в main.py колонка "Radius [km]" из planet_industry.csv
считывалась в DataFrame и нигде далее не использовалась. Это первый
модуль, который реально задействует эту колонку.

Как и в capacity.py — конкретная формула штрафа должна быть взята из
официальной механики EVE Online (радиус планеты и расстояние точки
привязки линка влияют на объём передачи ресурсов через Command Center
Link), а не подобрана на глаз.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LinkThroughput:
    base_units_per_hour: float
    penalty_factor: float  # 0..1, итоговая пропускная способность = base * penalty_factor

    @property
    def effective_units_per_hour(self) -> float:
        return self.base_units_per_hour * self.penalty_factor


def calculate_link_penalty(source_radius_km: float, target_radius_km: float, distance_km: float) -> float:
    """
    Вернуть коэффициент штрафа (0..1) для командного линка между двумя точками
    с учётом радиусов планет и расстояния между ними.

    Используется planner.py при выборе планеты под фабричный хаб —
    в v1 выбор хаба (см. main.py, calculate_plan -> factory_const) вообще
    не учитывал расстояния/радиус, просто брал первую подходящую систему.
    """
    raise NotImplementedError(
        "TODO(Фаза 1): реализовать по официальной формуле CCP, сверить перед внедрением"
    )
