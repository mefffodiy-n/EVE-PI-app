"""
Flask-приложение — только HTTP-слой.

ПРАВИЛА ПРОЕКТА, отражённые в этом модуле:

  1. Ни один обработчик не обращается к ESI — ни прямо, ни косвенно,
     ни «лениво при первом запросе». Все внешние вызовы живут в workers/
     и запускаются по расписанию (Фазы 2-3). Здесь только чтение того,
     что сборщики уже положили в БД/кэш.

  2. Никакого Docker. Разворачивается как обычное WSGI-приложение
     (gunicorn/waitress + nginx) — см. README.

  3. Приложение не должно тяжело нагружать сервер при большом числе
     пользователей. Flask синхронный: любая долгая работа в обработчике
     блокирует воркер целиком, поэтому:
       - справочные данные считаются один раз и отдаются из памяти
         процесса (см. api/cache.py);
       - тяжёлые pandas-операции не выполняются на каждый запрос —
         domain-загрузчики кэшированы через lru_cache;
       - расчёт плана кэшируется по параметрам запроса;
       - ответы справочников снабжаются ETag, чтобы браузер и прокси
         не дёргали сервер повторно.

Маршруты намеренно плоские (/api/initial-data, /api/calculate, ...) —
именно так их вызывает существующий web/index.html. Менять контракт
без нужды нельзя: рабочий фронтенд пришлось бы переписывать.
"""

from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, jsonify, send_from_directory

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app(config: dict | None = None) -> Flask:
    """
    Фабрика приложения.

    Фабрика, а не глобальный `app`, потому что так приложение можно
    создавать с разной конфигурацией в тестах, не трогая переменные
    окружения и не импортируя побочные эффекты.
    """
    app = Flask(__name__, static_folder=None)
    app.config.setdefault("JSON_SORT_KEYS", False)
    if config:
        app.config.update(config)

    from api.blueprints.export import bp as export_bp
    from api.blueprints.market import bp as market_bp
    from api.blueprints.plans import bp as plans_bp
    from api.blueprints.reference import bp as reference_bp
    from api.blueprints.status import bp as status_bp

    app.register_blueprint(reference_bp, url_prefix="/api")
    app.register_blueprint(plans_bp, url_prefix="/api")
    app.register_blueprint(market_bp, url_prefix="/api")
    app.register_blueprint(export_bp, url_prefix="/api")
    app.register_blueprint(status_bp, url_prefix="/api")

    _register_error_handlers(app)
    _register_dev_cors(app)
    _register_web(app)
    return app


def _register_dev_cors(app: Flask) -> None:
    """
    Разрешить кросс-доменные запросы — ТОЛЬКО для разработки вёрстки.

    Штатно фронтенд отдаётся этим же приложением, поэтому CORS не нужен:
    origin один и тот же. Но при правке вёрстки удобен Live Server с
    автоперезагрузкой, а он поднимается на другом порту, и без CORS
    браузер запросы заблокирует.

    Включается явно переменной окружения, не по умолчанию:
        PI_DEV_CORS=1 python run.py        (Windows: set PI_DEV_CORS=1)

    В production переменную не выставлять: разрешение принимать запросы
    с любого origin в бою — дыра, а не удобство.
    """
    if os.environ.get("PI_DEV_CORS", "").strip() not in ("1", "true", "yes"):
        return

    app.logger.warning(
        "PI_DEV_CORS включён: запросы разрешены с любого origin. "
        "Только для разработки, в production выключите."
    )

    @app.after_request
    def add_cors_headers(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return response

    @app.route("/api/<path:_ignored>", methods=["OPTIONS"])
    def cors_preflight(_ignored):
        return "", 204


def _register_error_handlers(app: Flask) -> None:
    """
    Единый формат ошибок.

    Фронтенд (web/index.html) везде проверяет поле `status`, поэтому
    ошибки отдаются в том же виде, что и успешные ответы, а не голым
    HTML-исключением Flask.
    """

    @app.errorhandler(400)
    def bad_request(err):
        return jsonify(status="error", message=str(getattr(err, "description", err))), 400

    @app.errorhandler(404)
    def not_found(err):
        return jsonify(status="error", message="Не найдено"), 404

    @app.errorhandler(500)
    def server_error(err):
        app.logger.exception("Необработанная ошибка")
        return jsonify(status="error", message="Внутренняя ошибка сервера"), 500


def _register_web(app: Flask) -> None:
    """
    Отдача фронтенда с того же origin.

    В v1 папка web/ существовала, но сервер её не отдавал: index.html
    открывали как локальный файл, отсюда хардкод
    API_BASE = 'http://localhost:8000/api' и вынужденный CORS "*".
    Отдавая статику отсюда, оба костыля можно убрать — фронт становится
    same-origin, CORS не нужен вовсе.

    В production статику лучше отдавать nginx'ом напрямую, минуя Python:
    это заметно снижает нагрузку при большом числе пользователей.
    """
    if not WEB_DIR.is_dir():
        return

    @app.route("/")
    def index():
        return send_from_directory(WEB_DIR, "index.html")

    @app.route("/<path:filename>")
    def web_files(filename: str):
        return send_from_directory(WEB_DIR, filename)


app = create_app()


if __name__ == "__main__":
    # Только для разработки. В production — gunicorn/waitress,
    # см. README (раздел «Запуск»).
    app.run(host="127.0.0.1", port=8000, debug=True)
