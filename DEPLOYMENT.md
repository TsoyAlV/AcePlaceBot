# Развёртывание Ace Place Bot на VPS

Инструкция для переноса работающего проекта с Windows на VPS **151.243.177.216** с **Ubuntu 22.04 LTS** и диском 60 ГБ.

Репозиторий: <https://github.com/TsoyAlV/AcePlaceBot>. Приложение и PostgreSQL запускаются через Docker Compose. Python на самом VPS устанавливать не требуется: образ содержит Python 3.12.10.

**Домен и входящий HTTPS не нужны.** Бот использует long polling и сам обращается к Telegram и Google по интернету. PostgreSQL остаётся доступен только локально на сервере, по `127.0.0.1:15432`.

Это инструкция, а не автоматический скрипт: выполняйте шаги последовательно и при ошибке остановитесь. Рабочие пароли и токены не добавляйте в Git.

## Где выполнять команды

| Пометка | Где выполнять |
| --- | --- |
| **VPS / Bash** | В терминале SSH-сессии MobaXterm на сервере |
| **Windows / PowerShell** | На своём компьютере, в папке проекта |

Серверные команды ниже рассчитаны на пользователя `root`. Если вход выполнен под пользователем с sudo-доступом, сначала выполните `sudo -i`. Для SCP используйте тот SSH-логин, который выдал провайдер; примеры ниже используют `root` и порт 22.

## План переноса

1. Подключиться к VPS и установить Docker.
2. Скачать проект, перенести настройки и собрать образ, пока локальный бот ещё работает.
3. Остановить локального бота и все локальные процессы, меняющие брони.
4. Экспортировать текущую БД и перенести актуальный Google-токен.
5. Восстановить БД на VPS, проверить подключения и запустить бота.
6. Проверить работу из Telegram.

На одном токене должен работать **только один polling-процесс**. Локальную БД при переносе не удаляем. Разовый экспорт ниже нужен для миграции, а не для настройки периодического резервного копирования.

## 1. Подключение и проверка сервера

**MobaXterm:** Session → SSH → Remote host `151.243.177.216` → Specify username → логин провайдера → Port `22` либо порт из панели провайдера.

После входа:

**VPS / Bash:**

```bash
whoami
cat /etc/os-release
uname -m
free -h
df -h /
```

Проверьте фактическую RAM и свободное место: заявленные 60 ГБ не показывают, сколько диска уже занято. Если на сервере уже работают другие приложения, учтите их до установки пакетов и запуска контейнеров.

Убедитесь, что сервер имеет исходящий доступ к GitHub, репозиториям пакетов, Docker Hub, Telegram и Google. Для управления достаточно входящего SSH; открывать наружу 5432, 15432 или 8000 не нужно. В этой инструкции настройки SSH и firewall не меняются.

## 2. Docker Engine и Compose

Если Docker уже установлен, сначала проверьте:

**VPS / Bash:**

```bash
docker --version
docker compose version
systemctl is-active docker
```

При работающих Engine и Compose переходите к шагу 3. Следующие команды — установка из официального apt-репозитория на новом Ubuntu-сервере. Если ранее установлен `docker.io`, старый Compose, Podman или отдельный containerd, сначала разберите конфликты по [инструкции Docker](https://docs.docker.com/engine/install/ubuntu/); не удаляйте используемые пакеты вслепую.

**VPS / Bash:**

```bash
apt-get update
apt-get install -y ca-certificates curl git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
```

Добавьте репозиторий для установленного выпуска Ubuntu:

```bash
aceUbuntuCodename=$(. /etc/os-release; printf '%s' "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
aceDockerArch=$(dpkg --print-architecture)
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${aceUbuntuCodename}
Components: stable
Architectures: ${aceDockerArch}
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
docker --version
docker compose version
```

Выполняйте блок с `EOF` целиком. Если apt сообщает об ошибке репозитория или ключа, исправьте её до продолжения.

## 3. Скачать проект

**VPS / Bash:**

```bash
mkdir -p /opt
cd /opt
git clone https://github.com/TsoyAlV/AcePlaceBot.git ace-place
cd /opt/ace-place
git status --short
git log -1 --oneline
install -d -m 700 secrets
install -d -m 700 secrets/calendars
install -d -m 700 /root/ace-place-transfer
```

Если `/opt/ace-place` уже существует, не клонируйте поверх неё: сначала проверьте содержимое и состояние Git. Если репозиторий закрыт, настройте доступ GitHub через SSH deploy key или предусмотренную GitHub аутентификацию; пароль/токен не вставляйте в URL репозитория.

## 4. Перенести настройки и подготовить образ

В Git нет рабочего `.env`, `config.yaml` и Google-токена. Для переноса действующего бота нужны именно текущие рабочие файлы, а не шаблоны.

**Windows / PowerShell**, из корня проекта:

```powershell
scp .\.env root@151.243.177.216:/opt/ace-place/.env
scp .\config.yaml root@151.243.177.216:/opt/ace-place/config.yaml
```

Если используется нестандартный SSH-порт, добавьте к `scp` параметр `-P НОМЕР_ПОРТА`. Если команда `scp` на Windows отсутствует, загрузите эти два файла через SFTP-панель MobaXterm в `/opt/ace-place`; убедитесь, что скрытый файл `.env` также передан.

**VPS / Bash:**

```bash
cd /opt/ace-place
chmod 600 .env
chmod 644 config.yaml
docker compose --profile bot config --quiet
docker compose build bot
```

На этом этапе бота на VPS не запускайте.

Проверьте настройки редактором, например `nano .env` и `nano config.yaml`:

- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` должны описывать целевую БД. При копировании локального `.env` значения можно оставить прежними.
- `TELEGRAM_TEST` должен содержать тот токен, с которым бот уже работает. Название переменной сохраняется и на VPS; `TELEGRAM_PROD` код пока не использует.
- `GOOGLE_CALENDAR_ID` либо старый `GOOGLE_CALENDAR` должны указывать на прежний календарь.
- В `config.yaml` сохраняются ваши тарифы, уборки, реквизиты и инструкции посещения.
- Для используемой интеграции нужны `calendar.enabled: true` и `calendar.json_dir: secrets/calendars`.

Compose сам передаёт боту `POSTGRES_HOST=db`, `POSTGRES_PORT=5432`, `GOOGLE_TOKEN_FILE=/app/secrets/calendars/google_token.json`. Локальный адрес `127.0.0.1:15432` внутри контейнера для подключения к БД не используется.

**Не выводите `docker compose config` без `--quiet` в публикуемые логи:** развёрнутый конфиг содержит значения переменных окружения.

Для ограничения размера контейнерных логов на VPS можно создать локальный override:

**VPS / Bash:**

```bash
cat > /opt/ace-place/compose.override.yaml <<'EOF'
services:
  bot:
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
  db:
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
EOF
docker compose --profile bot config --quiet
```

Compose подхватывает этот файл автоматически. Он задаёт ротацию логов, не меняя данные и конфиги приложения.

## 5. Остановить локального бота и экспортировать БД

С этого момента начинается короткая пауза в обслуживании.

Если локальный бот работает в терминале Windows — остановите его через Ctrl+C. Если в Docker:

**Windows / PowerShell:**

```powershell
docker compose stop bot
docker compose up -d --wait db
```

Остановите также отдельный `app.expiry_worker`, если запускали его вручную. До окончания переноса не используйте изменяющие команды CLI и не запускайте локального бота заново.

Создайте согласованный дамп **внутри контейнера PostgreSQL 18**, затем скопируйте его на Windows:

```powershell
$aceTransferDir = Join-Path $env:TEMP 'ace-place-transfer'
New-Item -ItemType Directory -Force -Path $aceTransferDir | Out-Null
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --schema=public --no-owner --no-acl --file=/tmp/ace_place_migration.dump'
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL export failed' }
docker compose cp db:/tmp/ace_place_migration.dump (Join-Path $aceTransferDir 'ace_place_migration.dump')
if ($LASTEXITCODE -ne 0) { throw 'Dump copy failed' }
Get-Item (Join-Path $aceTransferDir 'ace_place_migration.dump') | Select-Object Name, Length
```

Архив включает схему `public`, данные, последовательности, права приложения в таблице `admins` и метаданные синхронизации. Посторонние тестовые схемы не переносятся. Для бинарного дампа не используйте перенаправление `>` или текстовый PowerShell-конвейер: `docker compose cp` сохраняет файл без перекодирования.

Формат архива и перенос через `pg_restore` описаны в [документации PostgreSQL](https://www.postgresql.org/docs/18/app-pgdump.html).

Зафиксируйте количество записей для сверки:

```powershell
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT (SELECT count(*) FROM public.clients) AS clients, (SELECT count(*) FROM public.admins) AS admins, (SELECT count(*) FROM public.bookings) AS bookings;"'
```

Передайте архив и актуальный токен после остановки локального бота:

```powershell
scp (Join-Path $aceTransferDir 'ace_place_migration.dump') root@151.243.177.216:/root/ace-place-transfer/ace_place_migration.dump
scp .\secrets\calendars\google_token.json root@151.243.177.216:/opt/ace-place/secrets/calendars/google_token.json
```

Если локальный токен хранится в другом месте, используйте фактический путь из `GOOGLE_TOKEN_FILE`. Через SFTP MobaXterm можно передать те же файлы в указанные папки. Для работы на сервере достаточно `google_token.json`; Desktop OAuth JSON оставьте на Windows для последующей интерактивной авторизации.

## 6. Права на Google-токен

В образе бот работает от UID **10001**. Он должен читать токен и создавать временный файл в этом же каталоге при обновлении.

**VPS / Bash:**

```bash
cd /opt/ace-place
chown -R 10001:10001 /opt/ace-place/secrets/calendars
chmod 700 /opt/ace-place/secrets
chmod 700 /opt/ace-place/secrets/calendars
chmod 600 /opt/ace-place/secrets/calendars/google_token.json
```

Каталог `secrets` на хосте может принадлежать root: Docker монтирует вложенную папку непосредственно в контейнер. Не делайте токен общедоступным через `chmod 777`.

## 7. Восстановить БД на VPS

**VPS / Bash:**

```bash
cd /opt/ace-place
chmod 600 /root/ace-place-transfer/ace_place_migration.dump
docker compose up -d --wait db
```

Проверьте, что целевая БД новая и в `public` пока нет таблиц:

```bash
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM pg_tables WHERE schemaname = '\''public'\'';"'
```

Ожидаемый результат — `0`. Если число другое, **не продолжайте восстановление поверх существующих данных**: сначала выясните, какой volume и база используются. Не применяйте миграции перед восстановлением дампа: архив уже содержит схему.

Скопируйте архив в контейнер и восстановите:

```bash
docker compose cp /root/ace-place-transfer/ace_place_migration.dump db:/tmp/ace_place_migration.dump
docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-acl --exit-on-error --single-transaction /tmp/ace_place_migration.dump'
```

Продолжайте только при завершении без ошибок. Восстановление выполняется одной транзакцией и не использует удаление существующих таблиц. См. [параметры pg_restore](https://www.postgresql.org/docs/18/app-pgrestore.html).

Сверьте количество записей с Windows:

```bash
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT (SELECT count(*) FROM public.clients) AS clients, (SELECT count(*) FROM public.admins) AS admins, (SELECT count(*) FROM public.bookings) AS bookings;"'
docker compose --profile bot run --rm --no-deps bot python -m app.check_db
```

Ожидается `PostgreSQL: OK` и совпадение трёх счётчиков. Владелец, сотрудники, Telegram ID, заявки и ID календарных событий уже перенесены; повторное назначение владельца не требуется.

### Если нужна полностью новая пустая база

Это альтернативный путь, а не продолжение восстановления. Для проекта без переносимых данных пропустите импорт архива и примените миграции `001_initial`–`009_calendar_sync` по порядку, как в `README.md`. Затем создайте первого владельца. Не выполняйте одновременно и миграции начальной схемы, и импорт полного дампа.

## 8. Проверить Google и запустить бота

**VPS / Bash:**

```bash
cd /opt/ace-place
docker compose --profile bot run --rm --no-deps bot python -m app.google_calendar check
```

Ожидается `Calendar readable...`. Проверка может обновить Google-токен, поэтому права из шага 6 обязательны.

Если интеграция намеренно выключена, эту проверку можно пропустить; бронирование работает без Google Calendar. Включённая интеграция должна пройти проверку до запуска.

Убедитесь, что локальный бот остановлен, затем:

```bash
docker compose --profile bot up -d bot
docker compose ps
docker compose logs --tail 50 -f bot
```

Ctrl+C завершает просмотр логов, но не останавливает бота. Можно закрыть MobaXterm: контейнер продолжит работать.

Внутри бота автоматически работают доставка уведомлений, синхронизация календаря, напоминания об уборке и истечение заявок. Отдельные процессы FastAPI, expiry_worker, cron и systemd-сервис приложения для этого не нужны.

Состояние очередей перенесено вместе с БД. После запуска возможна доставка актуальных накопившихся уведомлений и обработка заявок, срок которых истёк во время остановки. Для Google используются прежние ID событий; создавать новый календарь не требуется.

## 9. Проверить работу из Telegram

Проверьте последовательно:

1. `/start` — меню и язык доступны.
2. `/admin` — у прежнего владельца сохранились права, видны сотрудники и существующие записи.
3. `/book` — календарь открывается, доступность соответствует перенесённым броням и уборкам.
4. На тестовом интервале создайте и снимите блокировку: событие появляется и удаляется из Google.
5. С тестовым клиентом проверьте заявку, получение скриншота, ручную проверку оплаты и подтверждение. Не записывайте реальную оплату без поступления денег; для изолированного теста используйте тестовый бот/отдельную БД.
6. После подтверждения клиент получает адрес и инструкции, в Google появляются аренда и уборка. Перенос и отмена корректируют оба события.

Для проверки счётчиков и подключений на этапе переноса реальные сообщения не нужны; пользовательские сценарии последнего шага уже создают настоящие записи и уведомления.

## 10. Автозапуск и обслуживание

В Compose уже задано `restart: unless-stopped`, а Docker включён в автозапуск. Проверить настройки можно без перезагрузки:

**VPS / Bash:**

```bash
systemctl is-enabled docker
docker inspect --format '{{.Name}}: {{.HostConfig.RestartPolicy.Name}}' ace-place-db-1 ace-place-bot-1
```

Контейнер, который был явно остановлен командой `stop`, не следует считать включённым в автозапуск. Для его запуска используйте `docker compose --profile bot up -d bot`.

Проверка после запланированной перезагрузки сервера: снова подключитесь по SSH, выполните `docker compose ps` в `/opt/ace-place` и проверьте ответ на `/start`. Не перезагружайте VPS во время экспорта или восстановления БД.

| Действие | Команда на VPS из `/opt/ace-place` |
| --- | --- |
| Статус | `docker compose ps` |
| Логи бота | `docker compose logs --tail 100 -f bot` |
| Перезапуск после изменения конфига | `docker compose restart bot` |
| Применение изменений `.env` | `docker compose --profile bot up -d --force-recreate bot` |
| Остановка только бота | `docker compose stop bot` |
| Повторный запуск | `docker compose --profile bot up -d bot` |
| Использование диска Docker | `docker system df` |
| Свободное место на VPS | `df -h /` |

Не используйте `docker compose down -v` для обычного обслуживания: этот флаг удаляет volume с БД. Не меняйте `POSTGRES_PASSWORD` только в `.env` у существующей БД: пароль пользователя PostgreSQL от этого не изменится.

### Обновление кода из GitHub

```bash
cd /opt/ace-place
git status --short
git pull --ff-only origin main
docker compose --profile bot up -d --build bot
docker compose logs --tail 50 bot
```

Если Git показывает конфликт локальных изменений, разберите его до обновления. Если в обновлении есть новые миграции, сначала остановите бота и примените только новые миграции по инструкции этого обновления, затем запускайте новый код. Не запускайте всю историю миграций повторно.

`.env`, `config.yaml` и токен остаются локальными файлами. После редактирования конфига через программу, заменяющую файл целиком, при необходимости пересоздайте контейнер с `--force-recreate`, чтобы обновить bind-mount.

### Повторная авторизация Google

Если токен отозван или истёк, остановите бота на VPS. На Windows выполните:

```powershell
.\.venv\Scripts\python.exe -m app.google_calendar auth
.\.venv\Scripts\python.exe -m app.google_calendar check
scp .\secrets\calendars\google_token.json root@151.243.177.216:/opt/ace-place/secrets/calendars/google_token.json
```

Локальный polling при этом не запускайте. На VPS повторите права из шага 6, выполните `app.google_calendar check` в одноразовом контейнере и снова запустите бота.

Для External OAuth-приложения со статусом **Testing** Google ограничивает refresh token семью днями; Calendar-доступ не относится к исключениям базового профиля. Для постоянной эксплуатации проверьте статус приложения и необходимые требования Google к Production, после смены статуса пройдите авторизацию заново. Сам перенос токена на VPS не устраняет это ограничение. [Правила срока жизни токенов Google](https://developers.google.com/identity/protocols/oauth2).

## 11. Возврат к локальному запуску

Если VPS ещё не принимал изменения бронирований, сначала остановите его бота:

**VPS / Bash:**

```bash
cd /opt/ace-place
docker compose stop bot
docker compose ps
```

Затем на Windows:

```powershell
docker compose --profile bot up -d bot
```

Или запустите локальный `python -m app.bot`, оставив контейнер бота остановленным.

**Если на VPS уже появились или изменились брони, локальная БД устарела.** Не переключайте клиентов на неё до переноса актуальной серверной БД обратно в отдельную пустую целевую БД. Данные Google Calendar не восстановят отсутствующие записи PostgreSQL. Обратный перенос требует такой же остановки записи и сверки данных, как шаги 5–7.

## 12. Частые ошибки

| Ошибка | Что проверить |
| --- | --- |
| SSH не подключается | Логин и SSH-порт из панели хостинга; состояние сервера и службы SSH |
| `Permission denied` при SCP | SSH-пользователя и права на целевую папку; у непривилегированного пользователя загрузите файл в его домашнюю папку, затем перенесите через sudo |
| Docker не найден или недоступен | Установку Engine/Compose; на VPS выполняйте команды из root-сессии или с sudo |
| Compose не находит `.env` или `config.yaml` | Текущий каталог `/opt/ace-place`, наличие скрытого `.env` и рабочих файлов |
| `ConnectionTimeout` | `docker compose ps`, готовность `db`, совпадение пользователя/пароля и переменные `db:5432` для контейнера |
| `pg_restore` сообщает, что объект существует | Целевая база не пустая либо перед импортом уже применены миграции; не добавляйте `--clean` без выяснения причины |
| Конфликт polling | Второй процесс на Windows, другом сервере или другом контейнере использует тот же токен |
| Не найден Google-токен | Файл `/opt/ace-place/secrets/calendars/google_token.json`, имя без лишнего расширения |
| Google выдаёт ошибку доступа/обновления | Авторизацию нужного аккаунта, ID календаря, срок токена и права UID 10001 на файл и каталог |
| Бот отвечает, но Google не обновляется | `calendar.enabled`, проверку `check`, состояние Calendar в административной карточке |
| Нет уведомлений | Запущенный бот, роль, `active`, персональный флаг и глобальный флаг напоминаний |
| Ручные правки Google пропадают | Источник данных — PostgreSQL; перенос и отмену выполняйте в боте |

## Краткий критерий завершения

Развёртывание закончено, когда на VPS запущены `db` и `bot`, локальный polling остановлен, счётчики перенесённых записей совпадают, прежний владелец входит в `/admin`, клиент видит корректную доступность, а Google Calendar проходит проверку и обновляется после действий в боте.
