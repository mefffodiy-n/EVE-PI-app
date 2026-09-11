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
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m scripts.extract_schematics --write

# .env — см. раздел 2, PI_DATABASE_URL на sqlite-файл в data/,
# PI_TOKEN_KEY сгенерировать, PI_BACKUP_DIR=/opt/pi-director/backups
set -a && source .env && set +a
.venv/bin/python -m alembic upgrade head

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
