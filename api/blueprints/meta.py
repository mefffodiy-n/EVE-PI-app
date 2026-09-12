"""
Метаданные приложения: версия, статус игрового сервера, состояние входа.

Всё здесь — ЧТЕНИЕ уже собранного. Ни один обработчик не ходит наружу:
счётчик онлайна кладёт в кэш scripts/refresh_server_status.py по
расписанию, а состояние входа определяется наличием настроек, а не
запросом к ESI.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint

from api.cache import json_ok, with_etag
from version import PHASE, VERSION

bp = Blueprint("meta", __name__)

ROOT = Path(__file__).resolve().parent.parent.parent
STATUS_SNAPSHOT = ROOT / "data" / "cache" / "server_status.json"

# Снапшот старше этого срока показываем как устаревший: лучше честно
# сказать «данные несвежие», чем выдавать вчерашний онлайн за текущий.
STALE_AFTER_MINUTES = 30


def _server_status() -> dict:
    if not STATUS_SNAPSHOT.is_file():
        return {
            "available": False,
            "reason": "no_snapshot",
            "hint": "Запустите scripts/refresh_server_status.py и поставьте его в расписание.",
        }

    try:
        snapshot = json.loads(STATUS_SNAPSHOT.read_text(encoding="utf-8"))
    except Exception:
        return {"available": False, "reason": "unreadable"}

    collected = snapshot.get("collected_at")
    age_minutes = None
    if collected:
        try:
            moment = datetime.fromisoformat(collected)
            age_minutes = int((datetime.now(timezone.utc) - moment).total_seconds() // 60)
        except ValueError:
            pass

    if snapshot.get("error"):
        return {"available": False, "reason": snapshot["error"], "age_minutes": age_minutes}

    return {
        "available": snapshot.get("players") is not None,
        "players": snapshot.get("players"),
        "server_version": snapshot.get("server_version"),
        "age_minutes": age_minutes,
        "stale": age_minutes is not None and age_minutes > STALE_AFTER_MINUTES,
    }


JOB_STATUS_SNAPSHOT = ROOT / "data" / "cache" / "scheduler_status.json"

# Джоб считается устаревшим, если не запускался дольше двух своих
# интервалов — с запасом на то, что планировщик мог быть занят другим
# джобом или временно недоступен, не поднимая ложную тревогу на ровном
# месте (интервалы у джобов разные, от 10 минут до суток).
STALE_INTERVAL_MULTIPLIER = 2


def _job_status() -> dict:
    """
    Статус фоновых сборщиков (scripts/scheduler.py) — из снимка, который
    планировщик пишет сам после каждого запуска джоба
    (scheduler.py::_write_status_snapshot). Веб-процесс и планировщик —
    РАЗНЫЕ процессы (deploy/README.md), их общее состояние живёт только
    в памяти планировщика; читать готовый файл — не «ходить наружу»
    (правило 3), это то же самое чтение снимка, что и у _server_status().
    """
    if not JOB_STATUS_SNAPSHOT.is_file():
        return {"available": False, "reason": "no_snapshot"}

    try:
        snapshot = json.loads(JOB_STATUS_SNAPSHOT.read_text(encoding="utf-8"))
    except Exception:
        return {"available": False, "reason": "unreadable"}

    now = datetime.now(timezone.utc)
    jobs = []
    for job in snapshot.get("jobs", []):
        last_run = job.get("last_run")
        every_minutes = job.get("every_minutes") or 60
        age_minutes = None
        overdue = True  # ни разу не запускался — тоже устарел, не «ок» по умолчанию
        if last_run:
            try:
                moment = datetime.fromisoformat(last_run)
                age_minutes = int((now - moment).total_seconds() // 60)
                overdue = age_minutes > every_minutes * STALE_INTERVAL_MULTIPLIER
            except ValueError:
                pass

        failures = job.get("failures", 0)
        # Сбои — самостоятельный признак: джоб мог отработать совсем
        # недавно (задержка перед повтором сама по себе укладывается в
        # every_minutes) и всё равно быть не «ок», раз реально падает.
        status = "failing" if failures else ("stale" if overdue else "ok")
        jobs.append({
            "name": job.get("name"),
            "age_minutes": age_minutes,
            "failures": failures,
            "status": status,
        })

    worst = "ok"
    if any(j["status"] == "failing" for j in jobs):
        worst = "failing"
    elif any(j["status"] == "stale" for j in jobs):
        worst = "stale"

    return {"available": True, "worst": worst, "jobs": jobs}


def _auth_status() -> dict:
    """
    Готова ли настоящая авторизация через EVE SSO.

    Проверяется наличие настроек приложения (`infra.config`), а не
    обращение к ESI: без `client_id` вход невозможен в принципе.
    """
    from infra import config

    return {
        "sso_configured": config.sso_configured(),
        "dev_mode": config.IS_DEV,
    }


@bp.get("/meta")
def meta():
    """Версия, статус сервера, состояние входа — одним запросом."""
    return json_ok(
        version=VERSION,
        phase=PHASE,           # {ru, en} — фронт берёт по языку интерфейса
        server=_server_status(),
        auth=_auth_status(),
        jobs=_job_status(),
    )


@bp.get("/characters")
def characters():
    """
    Персонажи, доступные планировщику: и dev-заглушки, и вошедшие через
    EVE SSO (`source` в таблице `characters`). Фронтенду происхождение
    не видно — поля одинаковые.

    Реальные (esi) персонажи — только этого визита (api/session.py):
    load_characters() без account_id в проде честно отдаёт пусто, а не
    всех подряд (правило 1) — так до 11.09.2026 разные пользователи
    видели персонажей друг друга.

    esi_linked — источник персонажа (dev-заглушка/настоящий вход через
    SSO), нужен фронту только для кнопки «Отвязать персонажа»
    (/api/auth/unlink) — заглушку отвязывать нечего, она не проходила
    через SSO. domain/planner.py::CharacterSlot источник намеренно не
    знает (планировщику всё равно, откуда персонаж), поэтому читается
    отдельным запросом здесь, в API-слое, а не протаскивается через
    domain.

    needs_reconnect — у esi-персонажа нет строки в `credentials`: либо
    ни разу не проходил OAuth (не должно случаться для source="esi",
    но на всякий случай), либо `refresh_tokens.py` удалил её сам после
    отказа ESI (`invalid_grant` — пользователь отозвал доступ EVE или
    сменил пароль). sync_colony_status.py в этом случае молча
    пропускает персонажа (`get_access_token()` вернёт None, `sync_one()`
    — "skipped", не ошибка джоба) — без этого поля колонии такого
    персонажа тихо переставали бы обновляться, а чип «Сборщики»
    оставался бы зелёным.
    """
    from sqlalchemy import select

    from scripts.seed_dev_characters import load_characters

    from api.session import current_account_id
    from infra.db import session_scope
    from infra.models import Character, Credential

    crew = load_characters(current_account_id())
    ids = [c.character_id for c in crew] or [-1]
    with session_scope() as session:
        sources = dict(session.execute(
            select(Character.character_id, Character.source)
            .where(Character.character_id.in_(ids))
        ).all())
        credentialed = set(session.scalars(
            select(Credential.character_id).where(Credential.character_id.in_(ids))
        ).all())

    return json_ok(
        characters=[
            {
                "character_id": c.character_id,
                "name": c.name,
                "ccu": c.command_center_upgrades_level,
                "ic": c.interplanetary_consolidation_level,
                "planet_slots": c.planet_slots,
                "esi_linked": sources.get(c.character_id) == "esi",
                "needs_reconnect": sources.get(c.character_id) == "esi"
                    and c.character_id not in credentialed,
            }
            for c in crew
        ],
    )


@bp.get("/colonies")
def colonies():
    """
    Реальные колонии персонажей — снимок из ESI (sync_colony_status).

    Это НЕ расчётный план: план — «что стоит построить», здесь — «что
    построено». Фронтенд показывает отдельным блоком и подсвечивает
    планеты, где программа экстрактора вот-вот кончится.

    hours_left не считаем на сервере: фронтенд получает nearest_expiry
    и обновляет обратный отсчёт без повторного запроса.

    Колонии — только персонажей этого визита (api/session.py, тот же
    список, что отдаёт /api/characters): до 11.09.2026 сюда попадали
    колонии вообще всех пользователей приложения без разбора.
    """
    import copy

    from sqlalchemy import select
    from sqlalchemy.exc import OperationalError

    from api.session import current_account_id
    from infra.db import session_scope
    from infra.models import Character, Colony, ExtractionSample
    from scripts.seed_dev_characters import load_characters

    allowed_ids = {c.character_id for c in load_characters(current_account_id())}

    try:
        with session_scope() as session:
            names = dict(session.execute(select(Character.character_id, Character.name)).all())
            rows = session.scalars(
                select(Colony)
                .where(Colony.character_id.in_(allowed_ids or {-1}))
                .order_by(Colony.nearest_expiry.is_(None), Colony.nearest_expiry)
            ).all()

            # История добычи по каждому экстрактору — копится
            # sync_colony_status.py::record_extraction_samples(), ESI сама
            # историю не хранит. Один запрос на все колонии разом, группировка
            # по (character_id, planet_id, pin_id) в Python — таблица новая,
            # объём пока небольшой, отдельный запрос на пин был бы overkill.
            samples_by_pin: dict[tuple[int, int, int], list[ExtractionSample]] = {}
            for sample in session.scalars(
                select(ExtractionSample)
                .where(ExtractionSample.character_id.in_(allowed_ids or {-1}))
                .order_by(ExtractionSample.sampled_at)
            ):
                key = (sample.character_id, sample.planet_id, sample.pin_id)
                samples_by_pin.setdefault(key, []).append(sample)

            payload = []
            for row in rows:
                pins = copy.deepcopy(row.pins or [])
                for pin in pins:
                    if pin.get("kind") != "extractor_control_unit" or pin.get("pin_id") is None:
                        continue
                    key = (row.character_id, row.planet_id, int(pin["pin_id"]))
                    pin["extraction_history"] = [
                        {
                            "sampled_at": s.sampled_at.isoformat(),
                            "qty_per_cycle": s.qty_per_cycle,
                            "cycle_seconds": s.cycle_seconds,
                        }
                        for s in samples_by_pin.get(key, [])
                    ]
                payload.append({
                    "character": names.get(row.character_id, str(row.character_id)),
                    "character_id": row.character_id,
                    "planet_id": row.planet_id,
                    "planet_name": row.planet_name,
                    "system_name": row.system_name,
                    "planet_index": row.planet_index,
                    "planet_type": row.planet_type,
                    "upgrade_level": row.upgrade_level,
                    "num_pins": row.num_pins,
                    "structures": row.structures or [],
                    "pins": pins,
                    "cpu_percent": row.cpu_percent,
                    "pg_percent": row.pg_percent,
                    "cpu_used": row.cpu_used,
                    "cpu_capacity": row.cpu_capacity,
                    "pg_used": row.pg_used,
                    "pg_capacity": row.pg_capacity,
                    "nearest_expiry": row.nearest_expiry.isoformat() if row.nearest_expiry else None,
                    "synced_at": row.synced_at.isoformat() if row.synced_at else None,
                    # Когда ИГРА (не мы) в последний раз пересчитала колонию —
                    # last_cycle_start фабрик достоверен только на этот момент,
                    # не на текущее время (см. Colony.game_last_update).
                    "game_last_update": row.game_last_update.isoformat() if row.game_last_update else None,
                })
    except OperationalError:
        payload = []

    return json_ok(colonies=payload)
