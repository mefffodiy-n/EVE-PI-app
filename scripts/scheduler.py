"""
Планировщик фоновых сборщиков.

ЗАЧЕМ. Правило проекта: наружу ходят только сборщики по расписанию.
Их пять — статус сервера, рыночные цены, обновление ESI-токенов,
синхронизация скиллов и статуса колоний. Этот скрипт запускает их
с нужными интервалами одним процессом, с журналом в файл (Фаза 6).

ПОЧЕМУ БЕЗ APScheduler. Задача — вызвать несколько функций по таймеру.
Для этого хватает стандартной библиотеки; добавлять зависимость ради
тридцати строк цикла незачем, а в проекте и так есть правило не тащить
лишнее. Если расписание усложнится (разные календари, повторы при сбоях,
несколько процессов), APScheduler или Celery станут оправданы.

КОГДА ЭТОТ СКРИПТ НЕ НУЖЕН. Если система уже умеет запускать задачи по
расписанию — cron на Linux, планировщик заданий на Windows — надёжнее
использовать её: она переживает перезагрузку и ведёт свой журнал.
Тогда просто заведите два задания, как описано в самих сборщиках.
Этот скрипт удобен при разработке и на простом хостинге, где ставить
задания некуда.

Запуск:
    python -m scripts.scheduler              # обычный режим
    python -m scripts.scheduler --once       # один прогон всех сборщиков и выход
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Снимок для /api/meta (api/blueprints/meta.py::_job_status) — тот же
# приём, что у server_status.json (scripts/refresh_server_status.py):
# веб-процесс и планировщик — РАЗНЫЕ процессы (deploy/README.md), общее
# состояние Job.last_run/failures живёт только в памяти планировщика и
# без файла было бы недоступно веб-обработчику, а правило 3 запрещает
# ему ходить куда-либо, кроме чтения готового.
STATUS_SNAPSHOT = ROOT / "data" / "cache" / "scheduler_status.json"


@dataclass
class Job:
    name: str
    run: Callable[[], int]
    every_minutes: int
    reason: str

    last_run: float = 0.0
    failures: int = 0

    def due(self, now: float) -> bool:
        return now - self.last_run >= self.every_minutes * 60


def _server_status() -> int:
    from scripts.refresh_server_status import main as run
    return run()


def _market_prices() -> int:
    from scripts.refresh_market_prices import DEFAULT_STATION, main as run
    # Узел передаём явно: без аргумента сборщик посмотрел бы в sys.argv,
    # где лежат флаги планировщика, а не название узла.
    return run(DEFAULT_STATION)


def _refresh_tokens() -> int:
    from scripts.refresh_tokens import main as run
    return run()


def _sync_skills() -> int:
    from scripts.sync_character_skills import main as run
    return run()


def _sync_colonies() -> int:
    from scripts.sync_colony_status import main as run
    return run()


def _backup() -> int:
    from scripts.backup import main as run
    return run()


JOBS = [
    Job("Статус сервера", _server_status, 10,
        "число игроков онлайн меняется медленно, чаще опрашивать незачем"),
    Job("Рыночные цены", _market_prices, 60,
        "цены на продукцию PI устойчивы, а Fuzzwork — чужой сервис"),
    Job("Обновление токенов ESI", _refresh_tokens, 15,
        "access-токен живёт ~20 минут; продлеваем до истечения, "
        "пока пусто — мгновенный no-op"),
    Job("Скиллы персонажей", _sync_skills, 360,
        "уровни PI-скиллов меняются раз в дни; идёт ПОСЛЕ обновления токенов"),
    Job("Статус колоний", _sync_colonies, 30,
        "таймеры экстракторов; фронт считает обратный отсчёт сам, "
        "сбор нужен на случай перезапуска программы"),
    Job("Резервная копия", _backup, 1440,
        "раз в сутки: база и снимки кэша, старые чистятся"),
]

# Пауза после неудачи растёт, чтобы не долбить недоступный сервис.
# Потолок нужен, иначе после долгого простоя сборщик замолчит надолго.
BACKOFF_STEPS = [1, 5, 15, 30, 60]

log = None  # логгер; настраивается в main(), чтобы --once/тесты не плодили файл


def _write_status_snapshot() -> None:
    """
    Текущее Job.last_run/failures всех джобов — в файл, для /api/meta.
    Вызывается после КАЖДОГО запуска джоба (успешного или нет), поэтому
    снимок всегда отражает то, что планировщик знает о себе прямо сейчас,
    а не отстаёт до следующего полного цикла.
    """
    STATUS_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "jobs": [
            {
                "name": job.name,
                "every_minutes": job.every_minutes,
                "last_run": (
                    datetime.fromtimestamp(job.last_run, tz=timezone.utc).isoformat(timespec="seconds")
                    if job.last_run else None
                ),
                "failures": job.failures,
            }
            for job in JOBS
        ],
    }
    STATUS_SNAPSHOT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def run_job(job: Job) -> None:
    log.info("%s: запуск", job.name)
    try:
        code = job.run()
    except Exception:
        code = 1
        log.exception("%s: исключение", job.name)

    if code == 0:
        job.failures = 0
        log.info("%s: готово", job.name)
    else:
        job.failures += 1
        delay = BACKOFF_STEPS[min(job.failures - 1, len(BACKOFF_STEPS) - 1)]
        log.warning("%s: неудача №%d, следующая попытка не раньше чем через %d мин",
                    job.name, job.failures, delay)
        # Сдвигаем время последнего запуска вперёд: следующая попытка
        # произойдёт позже обычного, а не сразу на следующем тике.
        job.last_run = time.time() - job.every_minutes * 60 + delay * 60
        _write_status_snapshot()
        return

    job.last_run = time.time()
    _write_status_snapshot()


def main() -> int:
    global log
    from infra.logging import configure

    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true",
                        help="прогнать все сборщики один раз и выйти")
    args = parser.parse_args()

    log = configure("scheduler")

    if args.once:
        codes = []
        for job in JOBS:
            run_job(job)
            codes.append(job.failures)
        return 1 if any(codes) else 0

    log.info("Планировщик запущен. Остановка — Ctrl+C.")
    for job in JOBS:
        log.info("  · %s: раз в %d мин — %s", job.name, job.every_minutes, job.reason)

    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
        log.info("Останавливаюсь…")

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    # Первый прогон сразу: иначе после запуска приложение целый час
    # показывало бы «нет данных», хотя сборщик работает.
    for job in JOBS:
        run_job(job)

    while not stopping:
        now = time.time()
        for job in JOBS:
            if stopping:
                break
            if job.due(now):
                run_job(job)
        # Тик в секунду: интервалы измеряются минутами, точнее не нужно,
        # а частый опрос впустую греет процессор.
        time.sleep(1)

    log.info("Планировщик остановлен.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
