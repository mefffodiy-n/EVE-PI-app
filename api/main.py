"""
FastAPI-приложение — только HTTP-слой.

Правило (см. roadmap.md, раздел 0): ни один обработчик здесь не должен
вызывать ESI напрямую или косвенно. Все внешние вызовы — только в workers/
(появятся в Фазе 2-3). На Фазе 1 приложение вообще не содержит кода,
обращающегося к сети, кроме отдачи собственного API и статики.

ВАЖНО про префиксы: маршруты намеренно плоские (/api/initial-data,
/api/calculate, ...), а не сгруппированные (/api/reference/initial-data).
Так их вызывает существующий web/index.html — менять контракт без нужды
не будем, иначе рабочий фронтенд придётся переписывать.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routers import export, market, plans, reference

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="PI Director API")

# TODO(Фаза 3): сузить allow_origins до конкретного домена фронтенда.
# Пока "*" сохраняется осознанно: web/index.html открывается как локальный
# файл (origin = null / file://) и иначе не сможет обращаться к API.
# После монтирования статики (см. ниже) фронт можно открывать с того же
# origin — тогда CORS перестаёт быть нужен и это ограничение снимается.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(reference.router, prefix="/api", tags=["reference"])
app.include_router(plans.router, prefix="/api", tags=["plans"])
app.include_router(market.router, prefix="/api", tags=["market"])
app.include_router(export.router, prefix="/api", tags=["export"])

# Монтирование фронтенда. В v1 папка web/ существовала, но main.py её
# не отдавал — index.html приходилось открывать вручную как файл, из-за
# чего и потребовался CORS "*" и хардкод API_BASE = 'http://localhost:8000/api'.
# Смонтировав статику, оба этих костыля можно убрать (см. TODO во фронте).
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
