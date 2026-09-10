"""
Настройка журналирования.

ЗАЧЕМ. Приложение и сборщики должны оставлять след в файле, а не только
в консоли: на развёртывании терминала никто не смотрит, а разбирать сбой
сборщика по пустому месту нельзя (roadmap, Фаза 6).

Куда пишем: `{PI_LOG_DIR}/{component}.log`, ротация по размеру.
Уровень — `PI_LOG_LEVEL` (по умолчанию INFO). В консоль тоже пишем —
удобно при ручном запуске.

`configure()` идемпотентна: повторный вызов не плодит обработчики.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from infra import config

LOG_DIR = Path(os.environ.get("PI_LOG_DIR", config.ROOT / "data" / "logs"))
LEVEL = os.environ.get("PI_LOG_LEVEL", "INFO").upper()

_MAX_BYTES = 2 * 1024 * 1024
_BACKUPS = 5
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_configured: set[str] = set()


def configure(component: str) -> logging.Logger:
    """
    Подключить файловый и консольный обработчики к корневому логгеру.

    component — имя файла лога («scheduler», «web», «backup»).
    Возвращает логгер этого компонента.
    """
    if component not in _configured:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        root = logging.getLogger()
        root.setLevel(LEVEL)

        file_handler = RotatingFileHandler(
            LOG_DIR / f"{component}.log",
            maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(_FORMAT))
        file_handler.set_name(f"pi-file-{component}")
        root.addHandler(file_handler)

        if not any(h.get_name() == "pi-console" for h in root.handlers):
            console = logging.StreamHandler()
            console.setFormatter(logging.Formatter(_FORMAT))
            console.set_name("pi-console")
            root.addHandler(console)

        _configured.add(component)

    return logging.getLogger(component)
