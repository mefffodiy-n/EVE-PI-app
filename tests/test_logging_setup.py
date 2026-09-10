"""infra/logging: журнал в файл + консоль, идемпотентность."""

from __future__ import annotations

import logging

import pytest

from infra import logging as pilog


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(pilog, "LOG_DIR", tmp_path)
    monkeypatch.setattr(pilog, "_configured", set())
    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers = []
    yield tmp_path
    root.handlers = saved


def test_writes_to_component_file(_isolate):
    log = pilog.configure("scheduler")
    log.info("привет")
    for h in logging.getLogger().handlers:
        h.flush()
    assert "привет" in (_isolate / "scheduler.log").read_text(encoding="utf-8")


def test_configure_is_idempotent(_isolate):
    pilog.configure("web")
    n = len(logging.getLogger().handlers)
    pilog.configure("web")
    assert len(logging.getLogger().handlers) == n


def test_second_component_adds_only_a_file_handler(_isolate):
    pilog.configure("web")
    before = len(logging.getLogger().handlers)
    pilog.configure("backup")
    # +1 файловый, консоль общая
    assert len(logging.getLogger().handlers) == before + 1
    assert (_isolate / "backup.log").parent.is_dir()
