# Развёртывание PI Director

Обычное WSGI-приложение за nginx. Docker не используется (правило проекта 4).
Основная среда — Windows; на Linux всё то же, но служба заводится через
systemd, а расписание — через cron.

Два долгоживущих процесса:

| процесс | команда | что делает |
|---|---|---|
| веб | `python -m scripts.serve` | waitress на `PI_HOST:PI_PORT`, nginx проксирует |
| сборщики | `python -m scripts.scheduler` | опрос ESI/Fuzzwork по расписанию, бэкап раз в сутки |

Оба пишут журнал в `PI_LOG_DIR` (по умолчанию `data/logs/`), ротация по 2 МБ × 5.

---

## 1. Разовая подготовка

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m scripts.extract_schematics --write
.venv\Scripts\python -m alembic upgrade head
```

`.venv\Scripts\python -m scripts.diagnose` — проверяет, что данные и БД на месте.

## 2. Переменные окружения

Задаются в окружении службы (NSSM: вкладка *Environment*; systemd:
`Environment=`), НЕ в git. Минимум для прода:

```
PI_ENV=prod
PI_DATABASE_URL=postgresql+psycopg://user:pass@localhost/pidirector   # или оставить SQLite
PI_LOG_DIR=C:\ProgramData\PI-Director\logs
PI_BACKUP_DIR=C:\ProgramData\PI-Director\backups
```

Для входа через EVE SSO (Фаза 3) дополнительно:

```
PI_ESI_CLIENT_ID=...            # с developers.eveonline.com
PI_TOKEN_KEY=...                # python -c "from infra.crypto import generate_key; print(generate_key())"
PI_ESI_CALLBACK_URL=https://ваш-домен/api/auth/callback
```

`PI_ENV=prod` отключает dev-заглушки персонажей: планировщику нужны
настоящие персонажи из SSO.

## 3. Postgres (необязательно)

SQLite тянет один процесс без проблем, но веб и сборщики пишут в базу
одновременно. Под нагрузкой или при частых сборах поставьте Postgres:

```
PI_DATABASE_URL=postgresql+psycopg://user:pass@localhost/pidirector
.venv\Scripts\pip install psycopg[binary]
.venv\Scripts\python -m alembic upgrade head
```

ORM-модели те же — миграции применятся как есть.

## 4. Службы Windows (NSSM)

[NSSM](https://nssm.cc) оборачивает любую команду в службу с автозапуском
и журналом.

```
nssm install PI-Director-Web   "C:\path\.venv\Scripts\python.exe" "-m scripts.serve"
nssm set     PI-Director-Web   AppDirectory  C:\path
nssm set     PI-Director-Web   AppStdout     C:\ProgramData\PI-Director\logs\web-stdout.log
nssm set     PI-Director-Web   AppStderr     C:\ProgramData\PI-Director\logs\web-stderr.log

nssm install PI-Director-Jobs  "C:\path\.venv\Scripts\python.exe" "-m scripts.scheduler"
nssm set     PI-Director-Jobs  AppDirectory  C:\path
```

Переменные окружения — `nssm set <служба> AppEnvironmentExtra PI_ENV=prod ...`
или через `nssm edit`.

Linux: два unit-файла systemd с `ExecStart=/path/.venv/bin/python -m scripts.serve`
и `... -m scripts.scheduler`, `Restart=on-failure`.

## 5. Бэкап

`scripts.scheduler` делает копию раз в сутки сам. Если сборщики не крутятся
постоянно, заведите отдельное задание — образец `deploy/backup-task.xml`
(Task Scheduler → *Import Task*), путь к python и рабочему каталогу
поправьте под себя.

Postgres бэкапится не этим скриптом:

```
pg_dump pidirector > pi-%DATE%.sql
```

## 6. nginx

`deploy/nginx.conf.sample` — проксирует `/` на waitress, статику `web/`
отдаёт напрямую. TLS обязателен: `PI_ESI_CALLBACK_URL` должен быть `https`,
иначе EVE SSO отклонит.
