# Развёртывание PI Director

Обычное WSGI-приложение за nginx. Docker не используется (правило проекта 4).
Основная среда — Windows; на Linux всё то же, но служба заводится через
systemd, а расписание — через cron.

Два долгоживущих процесса:

| процесс | команда | что делает |
|---|---|---|
| веб | `python -m scripts.serve` | waitress на `PI_HOST:PI_PORT`, nginx проксирует |
| сборщики | `python -m scripts.scheduler` | опрос ESI/Fuzzwork/SDE по расписанию, бэкап раз в сутки |

Оба пишут журнал в `PI_LOG_DIR` (по умолчанию `data/logs/`), ротация по 2 МБ × 5.

С 21.09.2026 (Фаза 2 мультирегиональности) в число сборщиков входит
`scripts/refresh_sde.py` (скелет `regions`/`planets` по всем регионам New
Eden из официального SDE CCP, раз в 6 часов) — отдельного шага в деплое
он не добавляет, обычный джоб внутри уже перезапускаемого `scheduler`.
Единственное, что стоит знать заранее: на самом первом запуске после
этого PR (когда сохранённого билда SDE ещё нет) сборщик скачает архив
~95 МБ и вставит несколько десятков тысяч строк планет — не мгновенно
(на VPS — секунды-десятки секунд), но не блокирует веб-процесс (он в
отдельном процессе) и не требует ручных действий.

---

## 1. Разовая подготовка

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.lock.txt
.venv\Scripts\python -m scripts.extract_schematics --write
.venv\Scripts\python -m alembic upgrade head
.venv\Scripts\python -m scripts.migrate_planets_csv_to_db
```

Последний шаг — разовый перенос справочника планет (`data/
planet_industry.csv`, регион Fountain) в таблицы `regions`/`planets`
(Фаза 1 мультирегиональности, 21.09.2026): без него `alembic upgrade
head` создаёт таблицы пустыми, и `/api/initial-data` честно сообщит
`data_problems` вместо списка констелляций. Источник (сам CSV) — файл
в git, а не что-то, что нужно доставать заново на каждом сервере: он
приезжает вместе с `git clone`/`git pull`, поэтому переезд на другой
VPS не требует ничего особого — та же последовательность из четырёх
команд на пустом сервере, тот же файл уже в репозитории. Повторный
запуск без `--force` ничего не сломает — скрипт откажется дублировать
уже перенесённые строки.

`.venv\Scripts\python -m scripts.diagnose` — проверяет, что данные и БД на месте.

## 2. Переменные окружения

Задаются в окружении службы (NSSM: вкладка *Environment*; systemd:
`Environment=`), НЕ в git. Минимум для прода:

```
PI_ENV=prod
PI_DATABASE_URL=mysql+pymysql://user:pass@localhost/pidirector   # см. раздел 3 — или оставить SQLite
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

## 3. Выбор СУБД

Движок — только смена `PI_DATABASE_URL` + `alembic upgrade head`,
ORM-модели одни и те же для любого движка (`infra/db.py::_build_engine()`
выбирает драйвер по схеме URL, ничего специфичного для Postgres в
domain/api-слое нет). Но не все три движка одинаково готовы к проду —
ниже честно, что проверено, а что нет.

### 3.1. SQLite (по умолчанию)

Ничего не устанавливать — `PI_DATABASE_URL` не задан, приложение само
создаст `data/pi_director.db`. Годится для разработки и для маленького
однопользовательского прода без домена: тянет один процесс без проблем.
Ограничение — веб и сборщики пишут в базу одновременно, а с ростом
числа пользователей конкурентная запись становится узким местом (см.
docs/ROADMAP.md, Фаза 9); JSON-колонки (`pins`/`routes`/`structures`) на
SQLite — обычный `TEXT`, не индексируемый бинарный тип. Бэкап —
`scripts/backup.py` копирует файл через `sqlite3.Connection.backup()`
(согласованный снимок даже при работающем приложении), без установки
дополнительных пакетов.

### 3.2. MariaDB (прод с 20.09.2026)

```bash
sudo apt-get install -y mariadb-server
sudo mariadb -e "CREATE DATABASE pidirector CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
sudo mariadb -e "CREATE USER 'pidirector'@'localhost' IDENTIFIED BY '...';"
sudo mariadb -e "GRANT ALL PRIVILEGES ON pidirector.* TO 'pidirector'@'localhost';"
```

Дальше — обычное переключение приложения:

```
PI_DATABASE_URL=mysql+pymysql://user:pass@localhost/pidirector
.venv\Scripts\pip install pymysql
.venv\Scripts\python -m alembic upgrade head
```

Проверено на бою (миграция реального прода с Postgres, 20.09.2026):
схема, полный перенос данных, вход через SSO, все фоновые сборщики
(включая запись сложных JSON-колоний в `sync_colony_status`) и бэкап —
без единой ошибки. Если переключаетесь не на пустую базу, а переносите
уже накопленные данные — `python -m scripts.migrate_postgres_to_mysql
--postgres ... --mysql ...` ПОСЛЕ `alembic upgrade head` на пустой
MariaDB (создаёт только схему, не данные), см. докстринг скрипта; для
переноса с SQLite при отсутствии Postgres на пути — тот же принцип,
отдельного скрипта нет, годится `migrate_sqlite_to_postgres.py` как
образец (два движка вместо трёх строк меняются в `create_engine()`).
Бэкап — `mysqldump --single-transaction` + `gzip`, см. раздел 5.

### 3.3. Postgres (прод до 20.09.2026, полностью поддерживается)

Прод стоял на Postgres с 15.09.2026 по 20.09.2026 (`docs/ROADMAP.md`,
«Переход на Postgres» / «Миграция прода на MariaDB») — переключились на
MariaDB, не из-за проблем с Postgres, а по отдельному решению
пользователя; сама СУБД остаётся полностью рабочим вариантом,
установка та же:

```bash
sudo apt-get install -y postgresql
sudo -u postgres psql -c "CREATE USER pidirector WITH PASSWORD '...';"
sudo -u postgres psql -c "CREATE DATABASE pidirector OWNER pidirector;"
```

Дальше — обычное переключение приложения:

```
PI_DATABASE_URL=postgresql+psycopg://user:pass@localhost/pidirector
.venv\Scripts\pip install psycopg[binary]
.venv\Scripts\python -m alembic upgrade head
```

ORM-модели те же — миграции применятся как есть. Если переключаетесь
не на пустую базу, а переносите уже накопленные данные (персонажей,
сохранённые планы, историю добычи) — `python -m scripts.
migrate_sqlite_to_postgres --sqlite ... --postgres ...` ПОСЛЕ
`alembic upgrade head` на пустой Postgres (создаёт только схему, не
данные), см. докстринг скрипта. Бэкап — `pg_dump -Fc` (сжатый
custom-формат), см. раздел 5.

### 3.4. MySQL — теоретически совместим, отдельно не проверялся

SQLAlchemy и Alembic одинаково генерируют DDL для MySQL и MariaDB —
`PI_DATABASE_URL=mysql+pymysql://user:pass@localhost/pidirector`
работает (нужен драйвер: `pip install pymysql`), и весь код раздела 3.2
(включая бэкап) написан на общий диалект `mysql+`, не специфичный для
MariaDB. Отдельно на настоящем MySQL Server не проверялось — прод
мигрировал именно на MariaDB. JSON-колонки — `LONGTEXT` с CHECK, не
отдельный индексируемый тип (в отличие от Postgres `json`/`jsonb`) — не
мешает работе, но менее эффективно для будущих запросов по содержимому
JSON, если они когда-нибудь понадобятся.

**Бэкап:**
`mysqldump --single-transaction` (снимок InnoDB без блокировки таблиц)
+ сжатие `gzip` на стороне Python (у `mysqldump`, в отличие от
`pg_dump`, нет своего сжатого формата), файл `pidirector.sql.gz` в той
же папке `pi-backup-<ts>/`, что и у остальных движков. Восстановление:

```
gunzip -c pi-backup-*/pidirector.sql.gz | mysql -h хост -u пользователь -p имя_базы
```

Пароль читается из `PI_DATABASE_URL` и передаётся `mysqldump` через
переменную окружения `MYSQL_PWD`, не аргументом командной строки — не
виден в выводе `ps`.

Дальнейшая оптимизация при росте базы (не раньше, чем это реально
понадобится, — см. `docs/ROADMAP.md`, Фаза 10) — то же семейство
приёмов, что и для Postgres, только на стороне MySQL/MariaDB:
непрерывная архивация бинарного журнала (`log_bin`) между полными
дампами, либо Percona XtraBackup для физического инкрементального
бэкапа без остановки сервера.

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

`scripts.scheduler` делает копию раз в сутки сам — для всех трёх
поддерживаемых движков (раздел 3): SQLite через API `.backup()`,
Postgres через `pg_dump` (нужен в PATH, на Ubuntu ставится вместе с
пакетом `postgresql`), MySQL/MariaDB через `mysqldump` (нужен в PATH,
ставится вместе с пакетом `mysql-client`/`mariadb-client`). Если
сборщики не крутятся постоянно, заведите отдельное задание — образец
`deploy/backup-task.xml` (Task Scheduler → *Import Task*), путь к
python и рабочему каталогу поправьте под себя.

Восстановление dump-файла Postgres (сжатый custom-формат, `-Fc`, с
18.09.2026 — раньше был обычный текстовый SQL):

```
pg_restore --no-owner --clean --if-exists -d pidirector pi-backup-*/pidirector.dump
```

## 6. nginx

`deploy/nginx.conf.sample` — проксирует `/` на waitress, статику `web/`
отдаёт напрямую. TLS обязателен: `PI_ESI_CALLBACK_URL` должен быть `https`,
иначе EVE SSO отклонит.

---

## 7. Развёртывание на чистом Ubuntu VPS (проверено на бою)

Ниже — реальный порядок действий для минимального VPS (**1 CPU / 1 ГБ RAM /
10 ГБ SSD** тянет спокойно) без домена и без опыта администрирования.
Каждый шаг воспроизводился и проверялся на живом сервере.

### 7.1. Подготовка сервера (root, один раз)

```bash
# swap — обязателен при 1 ГБ RAM: pandas/numpy при сборке и pip-установке
# кратковременно требуют больше памяти, чем есть физически.
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile swap swap defaults 0 0' >> /etc/fstab
printf 'vm.swappiness=10\nvm.vfs_cache_pressure=50\n' > /etc/sysctl.d/99-swap.conf
sysctl -p /etc/sysctl.d/99-swap.conf

# непривилегированный пользователь для деплоя — root по SSH выключаем
useradd -m -s /bin/bash -G sudo deploy
mkdir -p /home/deploy/.ssh
cp ~/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys   # свой публичный ключ
chown -R deploy:deploy /home/deploy/.ssh && chmod 700 /home/deploy/.ssh && chmod 600 /home/deploy/.ssh/authorized_keys
echo 'deploy ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/deploy && chmod 440 /etc/sudoers.d/deploy
passwd -l deploy   # вход только по ключу
```

### 7.2. SSH-hardening + firewall + fail2ban

```bash
cat > /etc/ssh/sshd_config.d/99-hardening.conf <<'EOF'
Port 2222
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
MaxAuthTries 3
AllowUsers deploy
EOF
sshd -t && systemctl restart ssh   # ПРОВЕРИТЬ вход на новом порту, прежде чем закрывать 22!

apt-get install -y ufw fail2ban unattended-upgrades
ufw allow 2222/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable

cat > /etc/fail2ban/jail.local <<'EOF'
[sshd]
enabled = true
port = 2222
maxretry = 4
findtime = 10m
bantime = 1h
backend = systemd
EOF
systemctl enable --now fail2ban

printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' > /etc/apt/apt.conf.d/20auto-upgrades
systemctl enable --now unattended-upgrades
```

### 7.3. Бесплатный домен (DuckDNS) — если своего нет

1. Завести поддомен на <https://www.duckdns.org> (вход через существующий
   аккаунт — GitHub/Google/Reddit, без своего пароля на DuckDNS).
2. На сервере — автообновление A-записи на случай смены IP у VPS:

```bash
mkdir -p /etc/duckdns
cat > /etc/duckdns/duck.sh <<EOF
#!/bin/bash
TOKEN="<ваш-token-с-duckdns>"
DOMAIN="<поддомен>"
curl -fsS "https://www.duckdns.org/update?domains=\${DOMAIN}&token=\${TOKEN}&ip=" -o /var/log/duckdns.log
EOF
chmod 700 /etc/duckdns/duck.sh
/etc/duckdns/duck.sh
echo '*/5 * * * * root /etc/duckdns/duck.sh >/dev/null 2>&1' > /etc/cron.d/duckdns
```

### 7.4. Приложение + nginx + сертификат

```bash
# под пользователем deploy
sudo mkdir -p /opt/pi-director && sudo chown deploy:deploy /opt/pi-director
cd /opt/pi-director
git clone --depth 1 https://github.com/mefffodiy-n/EVE-PI-app.git app
cd app
python3 -m venv .venv && .venv/bin/pip install -r requirements.lock.txt
.venv/bin/python -m scripts.extract_schematics --write

# .env — см. раздел 2, PI_DATABASE_URL на sqlite-файл в data/,
# PI_TOKEN_KEY сгенерировать, PI_BACKUP_DIR=/opt/pi-director/backups
#
# ВАЖНО: .env обязательно источником (set -a && source .env && set +a)
# ПЕРЕД alembic — голый `.venv/bin/python -m alembic upgrade head` без
# этого не увидит PI_DATABASE_URL и молча смигрирует не ту БД (по
# умолчанию — SQLite-заглушку вместо настоящей MariaDB/Postgres из
# .env); проверено на бою 21.09.2026 — лог alembic должен показать
# `Context impl MySQLImpl`/`PostgresqlImpl`, не `SQLiteImpl`.
set -a && source .env && set +a
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m scripts.migrate_planets_csv_to_db   # регион Fountain — один раз на сервер

sudo apt-get install -y nginx certbot python3-certbot-nginx
# nginx.conf.sample → /etc/nginx/sites-available/pi-director,
# server_name и root-путь (/opt/pi-director/app/web) подставить,
# симлинк в sites-enabled, nginx -t, systemctl reload nginx
sudo certbot --nginx -d <ваш-поддомен> --non-interactive --agree-tos -m <ваш-email> --redirect
```

Юнит-файлы — как в разделе 4, `WorkingDirectory=/opt/pi-director/app`,
`EnvironmentFile=/opt/pi-director/app/.env`. На 1 ГБ RAM стоит добавить
ограничители, чтобы один процесс не уронил сервер целиком:

```ini
MemoryMax=400M
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/pi-director/app/data /opt/pi-director/logs /opt/pi-director/backups
ProtectHome=true
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pi-director-web pi-director-scheduler
curl -s https://<ваш-поддомен>/api/meta   # проверка
```
