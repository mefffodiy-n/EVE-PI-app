"""
api/blueprints/meta.py: /api/meta — версия, статус сервера, статус входа,
статус фоновых сборщиков.

Фокус этого файла — _job_status(): статус планировщика, который веб-процесс
узнаёт только из снимка (scripts/scheduler.py пишет его сам, отдельный
процесс — см. deploy/README.md). Без снимка или со старым снимком эндпоинт
обязан честно ответить, а не притвориться, что сборщики работают.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def client():
    from api import create_app

    app = create_app({"TESTING": True})
    with app.test_client() as test_client:
        yield test_client


def _write_snapshot(path, jobs, updated_at=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "updated_at": updated_at or datetime.now(timezone.utc).isoformat(),
        "jobs": jobs,
    }), encoding="utf-8")


def _job(name="Статус сервера", every_minutes=10, minutes_ago=0, failures=0):
    last_run = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    return {"name": name, "every_minutes": every_minutes, "last_run": last_run, "failures": failures}


class TestJobStatus:
    def test_no_snapshot_is_honestly_unavailable(self, client):
        """Свежий клон без запущенного планировщика — не выдумываем «всё ок»."""
        body = client.get("/api/meta").get_json()
        assert body["jobs"]["available"] is False
        assert body["jobs"]["reason"] == "no_snapshot"

    def test_unreadable_snapshot_is_honestly_unavailable(self, client):
        import api.blueprints.meta as meta

        meta.JOB_STATUS_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        meta.JOB_STATUS_SNAPSHOT.write_text("не json", encoding="utf-8")

        body = client.get("/api/meta").get_json()
        assert body["jobs"]["available"] is False
        assert body["jobs"]["reason"] == "unreadable"

    def test_fresh_jobs_report_ok(self, client):
        import api.blueprints.meta as meta

        _write_snapshot(meta.JOB_STATUS_SNAPSHOT, [
            _job("Статус сервера", every_minutes=10, minutes_ago=2),
            _job("Резервная копия", every_minutes=1440, minutes_ago=5),
        ])

        body = client.get("/api/meta").get_json()["jobs"]
        assert body["available"] is True
        assert body["worst"] == "ok"
        assert all(j["status"] == "ok" for j in body["jobs"])

    def test_job_overdue_by_more_than_double_its_interval_is_stale(self, client):
        """
        Джоб раз в 10 минут не запускался 25 минут (> 2x) — планировщик,
        похоже, не работает, а не просто «редкое расписание».
        """
        import api.blueprints.meta as meta

        _write_snapshot(meta.JOB_STATUS_SNAPSHOT, [
            _job("Статус сервера", every_minutes=10, minutes_ago=25),
        ])

        body = client.get("/api/meta").get_json()["jobs"]
        assert body["worst"] == "stale"
        assert body["jobs"][0]["status"] == "stale"

    def test_job_within_double_interval_is_not_stale_yet(self, client):
        """15 мин при интервале 10 — ещё не 2x, ложной тревоги быть не должно."""
        import api.blueprints.meta as meta

        _write_snapshot(meta.JOB_STATUS_SNAPSHOT, [
            _job("Статус сервера", every_minutes=10, minutes_ago=15),
        ])

        body = client.get("/api/meta").get_json()["jobs"]
        assert body["jobs"][0]["status"] == "ok"

    def test_failing_job_is_failing_even_if_recently_run(self, client):
        """
        Задержка перед повтором после сбоя сама укладывается в
        every_minutes (см. scheduler.py::run_job) — «недавно запускался»
        не значит «всё в порядке», раз он реально падает.
        """
        import api.blueprints.meta as meta

        _write_snapshot(meta.JOB_STATUS_SNAPSHOT, [
            _job("Обновление токенов ESI", every_minutes=15, minutes_ago=1, failures=3),
        ])

        body = client.get("/api/meta").get_json()["jobs"]
        assert body["worst"] == "failing"
        assert body["jobs"][0]["status"] == "failing"

    def test_never_run_job_is_not_reported_as_ok(self, client):
        """last_run=None (свежий процесс, до первого прогона) — не «ок» по умолчанию."""
        import api.blueprints.meta as meta

        _write_snapshot(meta.JOB_STATUS_SNAPSHOT, [
            {"name": "Скиллы персонажей", "every_minutes": 360, "last_run": None, "failures": 0},
        ])

        body = client.get("/api/meta").get_json()["jobs"]
        assert body["jobs"][0]["status"] == "stale"
        assert body["jobs"][0]["age_minutes"] is None

    def test_worst_status_wins_across_jobs(self, client):
        """Один упавший джоб красит всю сводку, даже если остальные в порядке."""
        import api.blueprints.meta as meta

        _write_snapshot(meta.JOB_STATUS_SNAPSHOT, [
            _job("Статус сервера", every_minutes=10, minutes_ago=1),
            _job("Обновление токенов ESI", every_minutes=15, minutes_ago=1, failures=1),
        ])

        body = client.get("/api/meta").get_json()["jobs"]
        assert body["worst"] == "failing"
