"""
Хранение планов производства.

ЗАЧЕМ. Планирование — это сравнение вариантов: «а если добавить
персонажа», «а если сменить домашнюю систему», «а если взять другой
продукт». Пока план живёт до перезагрузки страницы, сравнивать нечего.

ГДЕ ХРАНИМ. Файлы в data/plans/. Не в браузере — тогда план пропадал бы
при смене устройства и очистке кэша, а показать его напарнику было бы
нельзя. Не в базе — её в проекте пока нет, а заводить СУБД ради десятка
json-файлов рано: файлов будет столько же, сколько вариантов у одного
человека, то есть единицы.

Когда появится многопользовательский режим (Фаза 3, вместе с ESI SSO),
это место заменится на таблицу с привязкой к владельцу. Интерфейс
модуля рассчитан на такую замену: снаружи видны только функции
save/list/load/delete, а не пути к файлам.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLANS_DIR = ROOT / "data" / "plans"

# Ограничения — защита от разрастания и от мусора в именах.
MAX_PLANS = 50
MAX_NAME_LENGTH = 80
MAX_ROWS = 500

# Имя файла собирается из идентификатора, а не из названия плана:
# название вводит пользователь, и в нём может быть что угодно, включая
# слеши и точки, которыми легко выйти за пределы папки.
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


def _path(plan_id: str) -> Path:
    if not ID_PATTERN.match(plan_id):
        raise PlanStorageError(f"Недопустимый идентификатор плана: {plan_id!r}")
    return PLANS_DIR / f"{plan_id}.json"


def save(name: str, request: dict, rows: list[dict],
         warnings: list[str] | None = None,
         assumptions: list[str] | None = None) -> StoredPlan:
    """Сохранить план под заданным именем."""
    name = (name or "").strip() or f"План от {datetime.now().strftime('%d.%m %H:%M')}"
    if len(name) > MAX_NAME_LENGTH:
        name = name[:MAX_NAME_LENGTH].rstrip() + "…"
    if not rows:
        raise PlanStorageError("Пустой план сохранять нечего")
    if len(rows) > MAX_ROWS:
        raise PlanStorageError(f"Слишком большой план: {len(rows)} строк, максимум {MAX_ROWS}")

    existing = list_plans()
    if len(existing) >= MAX_PLANS:
        raise PlanStorageError(
            f"Сохранено уже {len(existing)} планов, это предел. "
            f"Удалите ненужные, чтобы освободить место."
        )

    plan = StoredPlan(
        id=uuid.uuid4().hex[:12],
        name=name,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        request=request or {},
        rows=rows,
        warnings=list(warnings or []),
        assumptions=list(assumptions or []),
    )
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    _path(plan.id).write_text(
        json.dumps(plan.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return plan


def load(plan_id: str) -> StoredPlan:
    path = _path(plan_id)
    if not path.is_file():
        raise PlanStorageError("План не найден — возможно, он был удалён")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PlanStorageError(f"Файл плана повреждён: {exc}") from exc
    return StoredPlan(
        id=raw.get("id", plan_id),
        name=raw.get("name", "без названия"),
        created_at=raw.get("created_at", ""),
        request=raw.get("request", {}),
        rows=raw.get("rows", []),
        warnings=raw.get("warnings", []),
        assumptions=raw.get("assumptions", []),
    )


def list_plans() -> list[StoredPlan]:
    """Все сохранённые планы, новые первыми."""
    if not PLANS_DIR.is_dir():
        return []
    plans: list[StoredPlan] = []
    for path in PLANS_DIR.glob("*.json"):
        try:
            plans.append(load(path.stem))
        except PlanStorageError:
            # Повреждённый файл не должен ронять весь список: остальные
            # планы читаются, а этот просто не показывается.
            continue
    plans.sort(key=lambda p: p.created_at, reverse=True)
    return plans


def delete(plan_id: str) -> bool:
    path = _path(plan_id)
    if not path.is_file():
        return False
    path.unlink()
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
