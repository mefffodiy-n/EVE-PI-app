"""
Admin-раздел: ручное заполнение плотности сырья и POCO по регионам —
Фаза 3 мультирегиональности (28.09.2026, docs/ROADMAP.md, Фаза 10).

ЗАЧЕМ. Ни ESI, ни SDE плотность сырья по планетам не публикуют (Фаза
2, scripts/refresh_sde.py — там же разбор источников). Единственный
способ, каким регион вообще может стать пригодным для расчёта плана —
кто-то вручную вписывает то, что узнал сканированием в игре или со
сторонней проверенной базы. Этот раздел — интерфейс для этого, не
замена ему: сам сбор данных остаётся ручным трудом, здесь только его
запись.

ДОСТУП. Список character_id — `infra.config.ADMIN_CHARACTER_IDS`
(`PI_ADMIN_CHARACTER_IDS` в окружении, через запятую). Правка справочника
планет влияет на расчёт ВСЕХ пользователей приложения, поэтому не
открыта всем подряд, как и предупреждает docs/ROADMAP.md. Неавторизо-
ванным — 404, не 403: раздел не должен быть виден даже по факту
существования маршрута (`require_admin` ниже, единая точка).

СТАТУС РЕГИОНА. `Region.status` переключается на `ready` ТОЛЬКО ручной
кнопкой в самой панели (`POST .../ready`) — никакого автоматического
порога по проценту заполнения: уточнено у пользователя 28.09.2026 —
admin сам решает, что данных достаточно, программа не гадает (правило
1 CLAUDE.md).
"""

from __future__ import annotations

import io
from functools import wraps

import pandas as pd
from flask import Blueprint, Response, abort, request

from api.cache import json_error, json_ok, parse_json_body
from domain.planets import (
    P2_DIRECT_RESOURCE_NAMES,
    POCO_RATE_COLUMN,
    R0_RESOURCE_NAMES,
    RADIUS_COLUMN,
    _clean_rows,
    _normalize_columns,
    _normalize_radius,
    load_region_for_admin,
)

bp = Blueprint("admin", __name__)


def _is_admin() -> bool:
    from api.session import current_account_id
    from infra import config
    from scripts.seed_dev_characters import load_characters

    if not config.ADMIN_CHARACTER_IDS:
        return False
    characters = load_characters(current_account_id())
    return any(c.character_id in config.ADMIN_CHARACTER_IDS for c in characters)


def require_admin(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not _is_admin():
            abort(404)
        return view(*args, **kwargs)
    return wrapper


@bp.get("/ping")
@require_admin
def ping():
    """
    Пустой ответ 200 для admin-персонажа, 404 (через require_admin) для
    остальных — фронтенд (web/index.html) дёргает этот маршрут один раз
    при загрузке страницы, чтобы решить, показывать ли кнопку «Admin-
    панель» рядом с «Добавить персонажа» (28.09.2026, по прямому
    запросу пользователя). Отдельный лёгкий маршрут, а не переиспользование
    /regions — не тянуть список регионов только ради проверки доступа.
    """
    return json_ok()


@bp.get("/regions")
@require_admin
def list_regions():
    from sqlalchemy import func, select

    from infra.db import session_scope
    from infra.models import Planet, Region

    with session_scope() as session:
        counts = dict(
            session.execute(
                select(Planet.region_id, func.count(Planet.id)).group_by(Planet.region_id)
            ).all()
        )
        regions = [
            {"id": r.id, "name": r.name, "status": r.status, "planet_count": counts.get(r.id, 0)}
            for r in session.scalars(select(Region).order_by(Region.name))
        ]
    return json_ok(regions=regions)


@bp.post("/regions/<int:region_id>/ready")
@require_admin
def mark_region_ready(region_id: int):
    from infra.db import session_scope
    from infra.models import Region

    with session_scope() as session:
        region = session.get(Region, region_id)
        if region is None:
            return json_error("Регион не найден", 404)
        region.status = "ready"
    from domain.planets import load_planets

    load_planets.cache_clear()
    return json_ok()


def _region_or_404(session, region_id: int):
    from infra.models import Region

    return session.get(Region, region_id)


@bp.get("/regions/<int:region_id>/template.csv")
@require_admin
def download_template(region_id: int):
    """
    CSV — снимок ТЕКУЩЕГО состояния БД для региона (не пустой скелет):
    уже известные радиус/тип/POCO/плотность заполнены, неизвестное —
    честно пустая ячейка. Та же структура колонок, что у `data/
    planet_industry.csv` — round-trip «скачал → дозаполнил недостающее
    → загрузил обратно» работает и на втором, третьем заходе.
    """
    from infra.db import session_scope

    with session_scope() as session:
        region = _region_or_404(session, region_id)
        if region is None:
            return json_error("Регион не найден", 404)
        region_name = region.name

    book = load_region_for_admin(region_id)
    df = book.dataframe

    columns = ["Constellation", "System", "Planet", "Type", RADIUS_COLUMN,
               POCO_RATE_COLUMN, "POCO Owner", *R0_RESOURCE_NAMES, *P2_DIRECT_RESOURCE_NAMES]
    out = pd.DataFrame(columns=columns)
    for col in columns:
        if col in df.columns:
            out[col] = df[col]
        else:
            out[col] = None

    buffer = io.StringIO()
    out.to_csv(buffer, sep=";", index=False)
    filename = f"{region_name}.csv".replace(" ", "_")
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@bp.post("/regions/<int:region_id>/upload")
@require_admin
def upload_region(region_id: int):
    """
    Загрузка заполненного CSV-шаблона обратно. Обновляет ТОЛЬКО
    заполненные (не NaN) ячейки — пустая ячейка не стирает уже
    сохранённое значение (важно при повторной частичной загрузке:
    «не знаю» и «ноль» — разные вещи, правило 1). Строки, для которых
    в этом регионе не нашлось планеты по (system, planet_number), не
    роняют запрос — честно собираются в `unmatched`.
    """
    from infra.db import session_scope
    from infra.models import Planet, Region

    if "file" not in request.files:
        return json_error("Нет файла ('file')")
    upload = request.files["file"]

    try:
        df = pd.read_csv(upload.stream, sep=";", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return json_error(f"Не удалось разобрать CSV: {type(exc).__name__}: {exc}")

    df = _normalize_columns(df)
    df = _clean_rows(df) if "Constellation" in df.columns else df
    df = _normalize_radius(df)

    updated = 0
    unmatched: list[dict] = []

    with session_scope() as session:
        region = _region_or_404(session, region_id)
        if region is None:
            return json_error("Регион не найден", 404)

        planets_by_key = {
            (p.system, p.planet_number): p
            for p in session.query(Planet).filter(Planet.region_id == region_id)
        }

        for _, row in df.iterrows():
            system = row.get("System")
            raw_number = row.get("Planet")
            if pd.isna(system) or pd.isna(raw_number):
                continue
            try:
                planet_number = int(round(float(raw_number)))
            except (TypeError, ValueError):
                continue

            planet = planets_by_key.get((str(system), planet_number))
            if planet is None:
                unmatched.append({"system": str(system), "planet": planet_number})
                continue

            radius = row.get(RADIUS_COLUMN)
            if pd.notna(radius):
                planet.radius_km = float(radius)
            poco_rate = row.get(POCO_RATE_COLUMN)
            if pd.notna(poco_rate):
                planet.poco_tax_rate = float(poco_rate)
            poco_owner = row.get("POCO Owner")
            if pd.notna(poco_owner):
                planet.poco_owner = str(poco_owner)

            r0 = dict(planet.r0_densities or {})
            for name in R0_RESOURCE_NAMES:
                value = row.get(name)
                if name in df.columns and pd.notna(value):
                    r0[name] = float(value)
            if r0:
                planet.r0_densities = r0

            p2 = dict(planet.p2_direct_densities or {})
            for name in P2_DIRECT_RESOURCE_NAMES:
                value = row.get(name)
                if name in df.columns and pd.notna(value):
                    p2[name] = float(value)
            if p2:
                planet.p2_direct_densities = p2

            updated += 1

    return json_ok(updated=updated, unmatched=unmatched)


@bp.get("/regions/<int:region_id>/planets")
@require_admin
def list_planets(region_id: int):
    """
    Все планеты региона для ручного редактирования по одной — без
    пагинации: admin-инструмент одного пользователя, счёт на сотни
    строк, не публичный трафик (правило 5 здесь не применимо).
    """
    from infra.db import session_scope
    from infra.models import Planet

    with session_scope() as session:
        planets = [
            {
                "id": p.id, "constellation": p.constellation, "system": p.system,
                "planet_number": p.planet_number, "planet_type": p.planet_type,
                "radius_km": p.radius_km, "poco_tax_rate": p.poco_tax_rate,
                "poco_owner": p.poco_owner, "r0_densities": p.r0_densities or {},
                "p2_direct_densities": p.p2_direct_densities or {},
            }
            for p in session.query(Planet)
            .filter(Planet.region_id == region_id)
            .order_by(Planet.system, Planet.planet_number)
        ]
    return json_ok(planets=planets)


@bp.patch("/planets/<int:planet_id>")
@require_admin
def update_planet(planet_id: int):
    from infra.db import session_scope
    from infra.models import Planet

    payload, error = parse_json_body({})
    if error:
        return json_error(error)

    with session_scope() as session:
        planet = session.get(Planet, planet_id)
        if planet is None:
            return json_error("Планета не найдена", 404)

        if "radius_km" in payload:
            planet.radius_km = payload["radius_km"]
        if "poco_tax_rate" in payload:
            planet.poco_tax_rate = payload["poco_tax_rate"]
        if "poco_owner" in payload:
            planet.poco_owner = payload["poco_owner"]
        if "r0_densities" in payload:
            planet.r0_densities = payload["r0_densities"]
        if "p2_direct_densities" in payload:
            planet.p2_direct_densities = payload["p2_direct_densities"]

    return json_ok()
