# Zanevka Newsbot — развёртывание и эксплуатация

Рабочая среда — Ubuntu RELEASE. Разработка ведётся в Codex Cloud; RELEASE используется только для эксплуатации. Общая архитектура — [system.md](../architecture/system.md), HTTP-контракт — [API](../ui/api.md), приёмка — [trial-run.md](trial-run.md). Развёртывание и изменение эксплуатационной конфигурации требуют отдельного согласования владельца.

## Действующая среда

| Параметр | Значение |
| --- | --- |
| Хост виртуализации | ELDAR, Windows, VirtualBox |
| Имя VM / hostname | mcpv2_RELEASE / mcpv3release |
| ОС / ресурсы | Ubuntu 24.04 LTS / 2 vCPU, 4 ГБ RAM |
| SSH с ELDAR | `ssh release_vm` (eldar@127.0.0.1:2223 → guest:22) |
| Каталог / учётная запись | /opt/newsbot / newsbot:newsbot (system account, nologin) |
| Интерпретатор | /opt/newsbot/.venv/bin/python (Python 3.12) |
| Системные зависимости | Pandoc, git, python3-venv, cron, Caddy |
| Службы | newsbot-webui.service, caddy.service, cron.service |
| Часовой пояс | Europe/Moscow |
| WebUI | https://10.10.5.82:8443 |
| Репозиторий | https://github.com/Ed1ch/Zanevka-newsbot |

Службы включены в автозагрузку. Фактические версии пакетов проверяются командами диагностики ниже; установленную версию кода определяет SHA Git, а не имя ветки.

## Первичное развёртывание

Команды для администратора Ubuntu при новой установке; не повторять создание пользователя и клонирование поверх действующей установки. Использовать согласованный SHA из main. Авторизация GitHub должна быть настроена для пользователя newsbot безопасным способом (не передавать токен в URL или аргументах команд). Приведённая процедура воспроизводит конфигурацию RELEASE; не является отчётом о повторной установке с нуля.

```sh
sudo apt install git python3 python3-venv pandoc cron caddy
sudo useradd --system --user-group --home-dir /opt/newsbot --shell /usr/sbin/nologin newsbot
sudo install -d -m 750 -o newsbot -g newsbot /opt/newsbot
sudo -u newsbot -H git clone https://github.com/Ed1ch/Zanevka-newsbot.git /opt/newsbot
# TARGET — согласованный полный SHA коммита main
sudo -u newsbot -H git -C /opt/newsbot switch --detach "$TARGET"
sudo -u newsbot -H python3 -m venv /opt/newsbot/.venv
sudo -u newsbot -H /opt/newsbot/.venv/bin/python -m pip install -r /opt/newsbot/requirements.txt
sudo -u newsbot -H sh -eu -c 'cd /opt/newsbot; umask 077; test -e config.py || cp config.example.py config.py; mkdir -p flags logs downloaded_eml extracted_images; chmod 600 config.py'
sudo -u newsbot -H /opt/newsbot/.venv/bin/python /opt/newsbot/webui_server.py init-admin
```

Перед разрешением запуска заполнить config.py рабочими параметрами локально, проверить webui_server.local.json, создать unit и настроить HTTPS (ниже). Не заменять существующие конфиги примерами. init-admin интерактивно запрашивает логин и пароль (минимум 5 символов), создаёт хеш и случайный ключ; пароля по умолчанию нет. Для смены пароля использовать тот же интерпретатор и `/opt/newsbot/webui_server.py change-password` от newsbot.

### Конфигурация и права

| Путь относительно /opt/newsbot | Владелец | Права на RELEASE |
| --- | --- | --- |
| . | newsbot:newsbot | 750 |
| config.py, webui_server.local.json | newsbot:newsbot | 600 |
| logs/, downloaded_eml/, extracted_images/ | newsbot:newsbot | 700 |
| flags/, .git/, .venv/, cron-newsbot.sh | newsbot:newsbot | 775 |

В новой установке права служебных каталогов могут быть строже; доступ нужен одной учётной записи ОС. Секреты, локальные конфиги, журналы, флаги и временные данные не коммитятся. Не удалять локальные данные через git clean -x/-X. Рабочий config.py использует отдельные присваивания литералов; API сохраняет только разрешённые поля. Ручные изменения применяются к следующему запуску. LOG_DIR задаётся локально; после его ручной смены требуется перезапуск WebUI.

IMAP_OUTPUT_DIR и OUTPUT_DIR должны указывать на выделенные рабочие каталоги (на RELEASE — downloaded_eml/ и extracted_images/). Очистка конвейера удаляет все обычные файлы в них, не только EML или изображения. Не указывать каталог проекта, docs/, stable_backup/, временные каталоги резервирования, каталоги конфигурации, venv, другие данные или ссылки на такие каталоги. Текущий валидатор IMAP_OUTPUT_DIR не исключает все опасные пути; не считать успешное сохранение подтверждением безопасности пути.

При первичной установке init-admin создаёт trusted_proxies=[]; перед запуском через Caddy обязательно задать trusted_proxies=["127.0.0.1"] в локальном JSON от newsbot. Несекретные параметры webui_server.local.json на RELEASE: host=127.0.0.1, port=8081, threads=12, prefix="", external_https=true, trusted_proxies=["127.0.0.1"], session_hours=8. Не заменять весь JSON этим перечнем — сохранить username, password_hash и session_key. Явный serve --local-http предназначен для локального loopback-стенда, не для эксплуатации.

## Systemd

Файл /etc/systemd/system/newsbot-webui.service:

```ini
[Unit]
Description=Zanevka Newsbot WebUI
After=network.target

[Service]
User=newsbot
Group=newsbot
WorkingDirectory=/opt/newsbot
ExecStart=/opt/newsbot/.venv/bin/python -u /opt/newsbot/webui_server.py serve
Restart=on-failure
RestartSec=5
KillMode=process
UMask=0077

[Install]
WantedBy=multi-user.target
```

При создании/изменении unit: `sudo systemctl daemon-reload`; включение WebUI: `sudo systemctl enable --now newsbot-webui`. KillMode=process сохраняет независимый дочерний конвейер при остановке/перезапуске WebUI; это не средство остановки бота. Перезапуск сервера отзывает активные сессии.

## Cron и ручной запуск

Системная зона RELEASE — Europe/Moscow; при новой установке администратор задаёт её через timedatectl. В crontab пользователя newsbot (редактор `sudo -u newsbot -H crontab -e`) должна быть ровно одна запись:

```cron
0 */2 * * * /bin/sh /opt/newsbot/cron-newsbot.sh
```

`sudo systemctl enable --now cron`. Не добавлять CRON_TZ, редиректы, flock или дополнительные команды в эту запись: API распознаёт точный абсолютный путь обёртки (также прямой вызов или sh). API читает только собственный crontab, кэширует результат на 30 секунд и не проверяет исправность cron.service. Без CRON_TZ поле timezone может быть null, хотя фактическая зона известна из ОС.

Обёртка проверяет flags/.newsbot.disabled (включая ссылку), использует .venv/bin/python и вызывает prelaunch.py. Переключатель WebUI создаёт/удаляет флаг, не меняет crontab и не прерывает текущую обработку. Ручной запуск разрешён независимо от флага:

```sh
sudo -u newsbot -H sh -c 'cd /opt/newsbot && exec .venv/bin/python -u prelaunch.py'
```

Ручной запуск выполняет реальную обработку, может публиковать новости и погасить delayed.publish; не использовать как безвредный health check.

## HTTPS и доступ из LAN

ELDAR 10.10.5.82:8443 → VirtualBox NAT 10.0.2.15:8443 → Caddy → Waitress 127.0.0.1:8081. Внутренний порт 8081 не пробрасывается. Настройка в /etc/caddy/Caddyfile (блок Newsbot; не перезаписывать другие сайты):

```caddyfile
{
    auto_https disable_redirects
}

https://10.10.5.82:8443 {
    bind 10.0.2.15
    tls internal
    reverse_proxy 127.0.0.1:8081 {
        flush_interval -1
    }
}
```

При изменении: `sudo caddy validate --config /etc/caddy/Caddyfile`, затем согласованное `sudo systemctl reload caddy` (при первом запуске enable --now). HTTPS и SSE идут через один origin; backend доверяет forwarding только от 127.0.0.1. Публичный снимок журналов до входа — причина сохранять ограничение доступа из LAN.

На ELDAR правило NAT VirtualBox newsbot-https: TCP 10.10.5.82:8443 → 10.0.2.15:8443. Правило Windows Firewall [AI] Newsbot HTTPS LAN: входящий TCP 8443 на 10.10.5.82, удалённые сети 10.10.5.0/24 и 10.10.6.0/24. UFW на RELEASE не активен; не считать его дополнительной защитой. Не менять пробросы Gateway и SSH при настройке Newsbot.

Клиентам передаётся только публичный корневой сертификат Caddy Local Authority из /var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt; установить его как доверенный CA штатным способом и проверить HTTPS без исключений сертификата. Приватный ключ CA не передавать. При восстановлении VM сохранить доверие к прежнему CA либо согласованно распространить новый корневой сертификат.

## Обновление

Обновление только до согласованного полного SHA main. Не разрабатывать на RELEASE. Если изменены только документы, развёртывание не требуется.

1. Записать исходное разрешение расписания в отчёт обслуживания (его же использовать при откате). Запретить плановые запуски через WebUI или флаг flags/.newsbot.disabled от newsbot. Исключить ручные запуски на время обслуживания, дождаться завершения конвейера, остановить WebUI (`sudo systemctl stop newsbot-webui`). Не убивать работающий процесс и не удалять lock-файл.
2. Под существующим общим lock проверить простой и чистоту tracked-файлов, получить main и проверить согласованный TARGET. Удерживать lock непрерывно до окончания резервного копирования, смены кода и установки зависимостей.
3. Создать новый комплект двух конфигов и SHA в .stable_backup.new/, проверить копии и только затем заменить stable_backup/. Для замены временно переименовать прежний комплект в .stable_backup.previous/. После успешной замены удалить прежний комплект. В конце успешной операции остаётся ровно один stable_backup/; архивы по датам не ведутся.
4. Переключить рабочее дерево на TARGET; при изменении requirements.txt обновить venv. После успешного выхода из команды lock освобождается. Запустить WebUI, проверить службы newsbot-webui, caddy, cron, HTTPS без ошибок сертификата, вход, историю и статус. Не запускать публикацию для проверки. Восстановить исходное разрешение расписания, проверить переключатель.
5. Зафиксировать предыдущий/новый SHA и результаты непосредственной проверки. Ждать очередного cron-запуска не требуется.

Перед выполнением задать TARGET в оболочке администратора. Проверить, что flags/ и flags/.main.lock не являются ссылками, флаг запрета установлен, WebUI остановлен. Команда ниже выполняется одним блоком; при любой ошибке не продолжать обновление и не включать расписание.

```sh
sudo -u newsbot -H flock -n /opt/newsbot/flags/.main.lock sh -eu -s -- "$TARGET" <<'SH'
cd /opt/newsbot
umask 077
target=$1
test -e flags/.newsbot.disabled || test -L flags/.newsbot.disabled || exit 1
test "$(.venv/bin/python -B -c 'import prelaunch; print(prelaunch.check_running())')" = idle
status=$(git status --porcelain --untracked-files=no)
test -z "$status"
git fetch origin main
test "$(git rev-parse --verify "$target^{commit}")" = "$target"
git merge-base --is-ancestor "$target" origin/main
old=$(git rev-parse HEAD)
changed=$(git diff --name-only "$old" "$target" -- requirements.txt)

# Не затирать остатки прерванной операции.
test ! -e .stable_backup.new
test ! -L .stable_backup.new
test ! -e .stable_backup.previous
test ! -L .stable_backup.previous
test ! -L stable_backup
mkdir -m 700 .stable_backup.new
install -m 600 config.py webui_server.local.json .stable_backup.new/
printf '%s\n' "$old" > .stable_backup.new/commit
cmp -s config.py .stable_backup.new/config.py
cmp -s webui_server.local.json .stable_backup.new/webui_server.local.json
test "$(cat .stable_backup.new/commit)" = "$old"
if [ -e stable_backup ]; then
    test -d stable_backup
    mv -T -- stable_backup .stable_backup.previous
fi
if ! mv -T -- .stable_backup.new stable_backup; then
    if [ -d .stable_backup.previous ]; then
        mv -T -- .stable_backup.previous stable_backup
    fi
    exit 1
fi
if [ -d .stable_backup.previous ]; then
    rm -r -- .stable_backup.previous
fi

git switch --detach "$target"
if [ -n "$changed" ]; then
    .venv/bin/python -m pip install -r requirements.txt
fi
SH
# Только после успешного завершения блока:
sudo systemctl start newsbot-webui
```

При ошибке создания нового комплекта прежний не заменяется. При прерывании между переименованиями или удалением старого комплекта не начинать новый backup: под тем же lock проверить наличие и целостность config.py, webui_server.local.json и commit в stable_backup/, .stable_backup.previous/ и .stable_backup.new/. Если stable_backup/ отсутствует, восстановить прежний комплект из .stable_backup.previous/ при его наличии. Если новый комплект уже установлен и проверен, удалить временный предыдущий; неполный staging удалить только после подтверждения сохранности пригодной копии. Не коммитить временные каталоги. После успешного восстановления оставить только один пригодный комплект. Не перезаписывать backup текущей неисправной версией.

Не удалять flags/, журналы, временные рабочие каталоги, локальные конфиги или stable_backup/ при смене кода. Не сбрасывать delayed.publish. Не выполнять force checkout/reset или git clean для устранения конфликтов. При неисправности оставить автоматические запуски выключенными до восстановления.

### Откат

При неудачном обновлении выполнить согласованный откат в том же режиме обслуживания (запрет расписания, простой конвейера, остановленный WebUI). **Не перезаписывать stable_backup при откате.** Удерживать общий lock тем же способом (flock на весь блок команд от newsbot). Взять SHA из stable_backup/commit, проверить наличие коммита и переключить дерево через git switch --detach. Восстановить два конфига через install -m 600 от newsbot из stable_backup/; если менялись зависимости, привести venv к требованиям возвращаемой версии (при необходимости пересоздать только venv). После успешного освобождения lock запустить WebUI, повторить непосредственную проверку и восстановить исходное разрешение расписания из отчёта обслуживания. Резервная копия не содержит точного снимка venv, почты, журналов или WordPress; откат кода не откатывает публикации.

## Обслуживание и диагностика

Команды чтения:

```sh
sudo systemctl is-active newsbot-webui caddy cron
sudo systemctl is-enabled newsbot-webui caddy cron
systemctl --failed
sudo ss -lntp
sudo -u newsbot -H crontab -l
timedatectl show -p Timezone
sudo -u newsbot -H git -C /opt/newsbot rev-parse HEAD
sudo -u newsbot -H /opt/newsbot/.venv/bin/python --version
pandoc --version
sudo -u newsbot -H /opt/newsbot/.venv/bin/python -m pip list
sudo journalctl -u newsbot-webui -n 50 --no-pager
sudo journalctl -u cron -n 30 --no-pager
df -h /opt/newsbot
```

Журналы конвейера в LOG_DIR (по умолчанию logs), ранние ошибки запуска — stderr терминала/cron или диагностические события WebUI. Настройки хранения: LOG_RETENTION_DAYS=30, LOG_MAX_TOTAL_MB=200 (МиБ), LOG_EMPTY_MIN_AGE_SECONDS=600 по умолчанию. Помеченные пустые журналы очищаются в начале следующих запусков; ограничения срока/объёма применяются также при закрытии журнала; не удалять активный журнал или lock. Резервная копия обновления — только один комплект stable_backup; не делать архивов venv или Git при каждом обновлении. При проблемах с публикацией сверить почту, WordPress и журнал; правила восстановления — [pipeline.md](../architecture/pipeline.md).

Офлайн-тесты и приёмка описываются в [trial-run.md](trial-run.md); не запускать рабочий конвейер вместо диагностических тестов.
