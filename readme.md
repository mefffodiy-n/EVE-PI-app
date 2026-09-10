# PI Director

Планировщик планетарного производства для EVE Online. Считает, сколько
колоний и каких нужно под заданные продукты, распределяет их по персонажам
и показывает, где план упирается в ограничения — число персонажей, размер
планет, уровень скиллов.

Веб-приложение на Flask, интерфейс — один файл `web/index.html` без сборки,
двуязычный (RU/EN). Данные для расчёта — статические, проверенные по
четырём источникам; ничего не выдумано (спорные места помечены в
`_source` / `assumptions` файлов данных).

## Состояние

| Фаза | Что | Статус |
|---|---|---|
| 1–2 | Расчёт, данные, фоновые сборщики цен и статуса | готово |
| 3 | Вход через EVE SSO, БД, синхронизация скиллов и колоний | **по коду готово; нужен `client_id` от CCP** |
| 4 | Прямое R0→P2, мультирегион, панель реальных колоний | готово |
| 5 | Двуязычие, дашборд, экспорт в Excel | готово |
| 6 | Служба (waitress), журнал, бэкапы, `deploy/` | **по коду готово; нужна установка на сервер** |

Подробно — `roadmap.md`. Правила и устройство проекта — `CLAUDE.md`.
Развёртывание — `deploy/README.md`.

## Быстрый старт (разработка)

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

.venv\Scripts\python -m scripts.extract_schematics --write   # data/schematics.json
.venv\Scripts\python -m alembic upgrade head                 # таблицы БД (SQLite)
.venv\Scripts\python -m scripts.seed_dev_characters          # dev-персонажи (PI_ENV=dev)

.venv\Scripts\python run.py
```

Откройте **http://127.0.0.1:8000/** — именно корень. Flask сам отдаёт
интерфейс.

Проверки:

```
.venv\Scripts\python -m pytest tests/ -q     # все тесты
.venv\Scripts\python -m scripts.diagnose     # данные, БД, эндпоинты
.venv\Scripts\python -m scripts.scheduler    # фоновые сборщики (с журналом)
```

### Live Server и `Unexpected token '<'`

Если открыть страницу через Live Server VS Code
(`127.0.0.1:5500/web/index.html`), она отдаст HTML, но про `/api` ничего
не знает и вернёт 404-страницу — фронтенд попробует разобрать её как JSON
и упадёт, списки останутся пустыми.

Нужен Live Server (автоперезагрузка при правке вёрстки) — запустите Flask
с разрешённым CORS:

```
set PI_DEV_CORS=1 && python run.py     # Windows
PI_DEV_CORS=1 python run.py            # Linux/macOS
```

Фронтенд сам определит другой origin. **В production переменную не
выставлять.**

## Production

`python -m scripts.serve` (waitress) вместо `run.py`, за nginx, оба
процесса (`serve` и `scheduler`) — службами. Пошагово — `deploy/README.md`.

Вход через EVE SSO требует переменных окружения `PI_ESI_CLIENT_ID`
(регистрация на developers.eveonline.com) и `PI_TOKEN_KEY`. Без них
`/api/auth/*` отвечает 503, остальное работает.

## Лицензия и права

EVE Online и логотип EVE — зарегистрированные торговые марки CCP hf.
Приложение сделано независимо, CCP hf его не поддерживает и не одобряет.
