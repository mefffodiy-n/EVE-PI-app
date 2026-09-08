"""
Точка входа для запуска приложения.

Разработка:
    python run.py

Production (Docker не используется — правило проекта):
    Windows:  waitress-serve --host=127.0.0.1 --port=8000 api:app
    Linux:    gunicorn -w 4 -b 127.0.0.1:8000 api:app

Про число воркеров. Flask синхронный: один воркер обслуживает один
запрос за раз. Тяжёлый здесь только расчёт плана, и он кэшируется
(api/cache.py), поэтому 2-4 воркера покрывают заметную нагрузку.
Статику лучше отдавать через nginx напрямую, минуя Python.
"""

from api import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
