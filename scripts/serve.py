"""
Production-запуск через waitress.

`python run.py` — только для разработки (debug-режим, автоперезагрузка).
На развёртывании нужен WSGI-сервер; Docker не используется (правило 4).

    python -m scripts.serve

Настройки — из окружения:
    PI_HOST     127.0.0.1   (наружу смотрит nginx, приложение — только локально)
    PI_PORT     8000
    PI_THREADS  4           (Flask синхронный; см. run.py про число воркеров)

nginx проксирует на PI_HOST:PI_PORT и отдаёт web/ напрямую —
пример конфигурации в deploy/nginx.conf.sample.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    from waitress import serve

    from api import create_app
    from infra.logging import configure

    log = configure("web")
    host = os.environ.get("PI_HOST", "127.0.0.1")
    port = int(os.environ.get("PI_PORT", "8000"))
    threads = int(os.environ.get("PI_THREADS", "4"))

    log.info("waitress: http://%s:%d, потоков %d", host, port, threads)
    serve(create_app(), host=host, port=port, threads=threads, ident="PI-Director")
    return 0


if __name__ == "__main__":
    sys.exit(main())
