"""
Планировщик фоновых сборщиков.

ЗАЧЕМ. Правило проекта: наружу ходят только сборщики по расписанию.
Их два — статус сервера и рыночные цены. Этот скрипт запускает их
с нужными интервалами одним процессом.

ПОЧЕМУ БЕЗ APScheduler. Задача — вызвать две функции по таймеру. Для
этого хватает стандартной библиотеки; добавлять зависимость ради
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
import signal
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


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
]

# Пауза после неудачи растёт, чтобы не долбить недоступный сервис.
# Потолок нужен, иначе после долгого простоя сборщик замолчит надолго.
BACKOFF_STEPS = [1, 5, 15, 30, 60]


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def run_job(job: Job) -> None:
    print(f"[{_stamp()}] {job.name}: запуск")
    try:
        code = job.run()
    except Exception:
        code = 1
        traceback.print_exc()

    if code == 0:
        job.failures = 0
        print(f"[{_stamp()}] {job.name}: готово")
    else:
        job.failures += 1
        delay = BACKOFF_STEPS[min(job.failures - 1, len(BACKOFF_STEPS) - 1)]
        print(f"[{_stamp()}] {job.name}: неудача №{job.failures}, "
              f"следующая попытка не раньше чем через {delay} мин")
        # Сдвигаем время последнего запуска вперёд: следующая попытка
        # произойдёт позже обычного, а не сразу на следующем тике.
        job.last_run = time.time() - job.every_minutes * 60 + delay * 60
        return

    job.last_run = time.time()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true",
                        help="прогнать все сборщики один раз и выйти")
    args = parser.parse_args()

    if args.once:
        codes = []
        for job in JOBS:
            run_job(job)
            codes.append(job.failures)
        return 1 if any(codes) else 0

    print("Планировщик запущен. Остановка — Ctrl+C.")
    for job in JOBS:
        print(f"  · {job.name}: раз в {job.every_minutes} мин — {job.reason}")
    print()

    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
        print(f"\n[{_stamp()}] Останавливаюсь…")

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

    print("Планировщик остановлен.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
