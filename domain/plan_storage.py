"""
Хранение планов производства.

ЗАЧЕМ. Планирование — это сравнение вариантов: «а если добавить
персонажа», «а если сменить домашнюю систему», «а если взять другой
продукт». Пока план живёт до перезагрузки страницы, сравнивать нечего.

ГДЕ ХРАНИМ. Таблица `plans` (`infra/models.py`). Не в браузере — тогда
план пропадал бы при смене устройства и очистке кэша, а показать его
напарнику было бы нельзя.

Раньше это были json-файлы в data/plans/. Замена на БД (Фаза 3): к плану
привяжется владелец (`account_id`, пока NULL). Публичный интерфейс —
save/list/load/delete/compare — не изменился, поэтому api/ и тесты
править не пришлось.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select

# Ограничения — защита от разрастания и от мусора в именах.
MAX_PLANS = 50
MAX_NAME_LENGTH = 80
MAX_ROWS = 500

# Идентификатор виден в URL и подставляется в запросы; проверяется строго.
ID_PATTERN = re.compile(r"^[0-9a-f]{12}$")


class PlanStorageError(ValueError):
    """Сохранить или прочитать план не удалось."""


@dataclass
class StoredPlan:
    id: str
    name: str
    created_at: str
    request: dict
    rows: list[dict]
    warnings: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        """Краткая карточка для списка — без строк плана, они тяжёлые."""
        mining = sum(1 for r in self.rows if "Добыча" in str(r.get("role", "")))
        peak = max(
            (max(r.get("cpu_percent", 0), r.get("pg_percent", 0)) for r in self.rows),
            default=0,
        )
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "colonies": len(self.rows),
            "mining": mining,
            "processing": len(self.rows) - mining,
            "characters": len({r.get("char_id") for r in self.rows}),
            "peak_load": round(peak, 1),
            "products": sorted({str(r.get("res_out")) for r in self.rows if r.get("res_out")}),
            "factory_system": self.request.get("factory_sys"),
            "warnings": len(self.warnings),
        }

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "request": self.request,
            "rows": self.rows,
            "warnings": self.warnings,
            "assumptions": self.assumptions,
        }

    @classmethod
    def _from_row(cls, row) -> "StoredPlan":
        return cls(
            id=row.id,
            name=row.name,
            created_at=row.created_at or "",
            request=row.request or {},
            rows=row.rows or [],
            warnings=row.warnings or [],
            assumptions=row.assumptions or [],
        )


def _valid_id(plan_id: str) -> str:
    if not ID_PATTERN.match(plan_id or ""):
        raise PlanStorageError(f"Недопустимый идентификатор плана: {plan_id!r}")
    return plan_id


def save(name: str, request: dict, rows: list[dict],
         warnings: list[str] | None = None,
         assumptions: list[str] | None = None) -> StoredPlan:
    """Сохранить план под заданным именем."""
    from infra.db import session_scope
    from infra.models import Plan

    name = (name or "").strip() or f"План от {datetime.now().strftime('%d.%m %H:%M')}"
    if len(name) > MAX_NAME_LENGTH:
        name = name[:MAX_NAME_LENGTH].rstrip() + "…"
    if not rows:
        raise PlanStorageError("Пустой план сохранять нечего")
    if len(rows) > MAX_ROWS:
        raise PlanStorageError(f"Слишком большой план: {len(rows)} строк, максимум {MAX_ROWS}")

    plan = StoredPlan(
        id=uuid.uuid4().hex[:12],
        name=name,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        request=request or {},
        rows=rows,
        warnings=list(warnings or []),
        assumptions=list(assumptions or []),
    )

    with session_scope() as session:
        count = session.scalar(select(func.count()).select_from(Plan)) or 0
        if count >= MAX_PLANS:
            raise PlanStorageError(
                f"Сохранено уже {count} планов, это предел. "
                f"Удалите ненужные, чтобы освободить место."
            )
        session.add(Plan(
            id=plan.id, name=plan.name, created_at=plan.created_at,
            request=plan.request, rows=plan.rows,
            warnings=plan.warnings, assumptions=plan.assumptions,
        ))

    return plan


def load(plan_id: str) -> StoredPlan:
    from infra.db import session_scope
    from infra.models import Plan

    _valid_id(plan_id)
    with session_scope() as session:
        row = session.get(Plan, plan_id)
        if row is None:
            raise PlanStorageError("План не найден — возможно, он был удалён")
        return StoredPlan._from_row(row)


def list_plans() -> list[StoredPlan]:
    """Все сохранённые планы, новые первыми."""
    from sqlalchemy.exc import OperationalError

    from infra.db import session_scope
    from infra.models import Plan

    try:
        with session_scope() as session:
            rows = session.scalars(
                select(Plan).order_by(Plan.created_at.desc())
            ).all()
            return [StoredPlan._from_row(r) for r in rows]
    except OperationalError:
        # Нет таблицы (свежий клон без `alembic upgrade`) — пустой список,
        # страница со списком планов должна открыться.
        return []


def delete(plan_id: str) -> bool:
    from infra.db import session_scope
    from infra.models import Plan

    _valid_id(plan_id)
    with session_scope() as session:
        row = session.get(Plan, plan_id)
        if row is None:
            return False
        session.delete(row)
    return True


def compare(left_id: str, right_id: str) -> dict:
    """
    Сравнить два плана.

    Показывает не только числа, но и что именно изменилось по колониям:
    сводка «на 3 планеты меньше» не отвечает на вопрос, каких именно.
    """
    left, right = load(left_id), load(right_id)

    def key(row: dict) -> tuple:
        return (str(row.get("system")), str(row.get("planet")), str(row.get("res_out")))

    left_keys = {key(r) for r in left.rows}
    right_keys = {key(r) for r in right.rows}

    def described(keys: set[tuple]) -> list[dict]:
        return [{"system": s, "planet": p, "product": o} for s, p, o in sorted(keys)]

    ls, rs = left.summary(), right.summary()
    return {
        "left": ls,
        "right": rs,
        "delta": {
            "colonies": rs["colonies"] - ls["colonies"],
            "characters": rs["characters"] - ls["characters"],
            "peak_load": round(rs["peak_load"] - ls["peak_load"], 1),
            "warnings": rs["warnings"] - ls["warnings"],
        },
        "only_in_left": described(left_keys - right_keys),
        "only_in_right": described(right_keys - left_keys),
        "unchanged": len(left_keys & right_keys),
    }
