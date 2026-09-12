"""
scripts/scheduler.py: run_job() и снимок статуса для /api/meta.

Планировщик и веб — разные процессы (deploy/README.md): Job.last_run/
failures живут только в памяти планировщика, и без файла-снимка веб-
процесс о них знать не может (api/blueprints/meta.py::_job_status
читает именно этот файл). Здесь проверяется, что снимок пишется после
каждого запуска джоба — успешного и неуспешного — и отражает то, что
run_job() реально сделал с Job.
"""

from __future__ import annotations

import json
import logging

import pytest

from scripts import scheduler as sched


@pytest.fixture(autouse=True)
def _isolate_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(sched, "STATUS_SNAPSHOT", tmp_path / "scheduler_status.json")
    sched.log = logging.getLogger("test-scheduler")


@pytest.fixture
def job():
    """Один изолированный Job — не трогаем реальный список JOBS целиком."""
    return sched.Job("Тестовый джоб", lambda: 0, every_minutes=10, reason="тест")


def _read_snapshot():
    return json.loads(sched.STATUS_SNAPSHOT.read_text(encoding="utf-8"))


class TestRunJobSnapshot:
    def test_success_resets_failures_and_writes_snapshot(self, job, monkeypatch):
        monkeypatch.setattr(sched, "JOBS", [job])
        sched.run_job(job)

        assert job.failures == 0
        assert job.last_run > 0

        snapshot = _read_snapshot()
        entry = snapshot["jobs"][0]
        assert entry["name"] == "Тестовый джоб"
        assert entry["failures"] == 0
        assert entry["last_run"] is not None

    def test_exception_counts_as_failure_and_is_snapshotted(self, monkeypatch):
        def boom():
            raise RuntimeError("сеть недоступна")

        job = sched.Job("Падающий джоб", boom, every_minutes=10, reason="тест")
        monkeypatch.setattr(sched, "JOBS", [job])
        sched.run_job(job)

        assert job.failures == 1
        snapshot = _read_snapshot()
        assert snapshot["jobs"][0]["failures"] == 1

    def test_nonzero_exit_code_counts_as_failure(self, monkeypatch):
        job = sched.Job("Падающий джоб", lambda: 1, every_minutes=10, reason="тест")
        monkeypatch.setattr(sched, "JOBS", [job])
        sched.run_job(job)

        assert job.failures == 1
        assert _read_snapshot()["jobs"][0]["failures"] == 1

    def test_repeated_failures_accumulate(self, monkeypatch):
        job = sched.Job("Падающий джоб", lambda: 1, every_minutes=10, reason="тест")
        monkeypatch.setattr(sched, "JOBS", [job])
        sched.run_job(job)
        sched.run_job(job)
        sched.run_job(job)

        assert job.failures == 3
        assert _read_snapshot()["jobs"][0]["failures"] == 3

    def test_success_after_failure_resets_to_zero(self, monkeypatch):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            return 1 if calls["n"] == 1 else 0

        job = sched.Job("Нестабильный джоб", flaky, every_minutes=10, reason="тест")
        monkeypatch.setattr(sched, "JOBS", [job])
        sched.run_job(job)
        assert job.failures == 1

        sched.run_job(job)
        assert job.failures == 0
        assert _read_snapshot()["jobs"][0]["failures"] == 0

    def test_snapshot_carries_every_minutes_for_staleness_check(self, job, monkeypatch):
        """
        api/blueprints/meta.py::_job_status сравнивает возраст с
        every_minutes — без него в снимке сравнивать не с чем.
        """
        monkeypatch.setattr(sched, "JOBS", [job])
        sched.run_job(job)
        assert _read_snapshot()["jobs"][0]["every_minutes"] == 10
