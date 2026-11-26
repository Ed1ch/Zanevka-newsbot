# Zanevka Newsbot — развёртывание и эксплуатация

Рабочая среда — Ubuntu RELEASE. Разработка ведётся в Codex Cloud; RELEASE используется только для эксплуатации. Общая архитектура — [system.md](../architecture/system.md), HTTP-контракт — [API](../../webui/API.md), приёмка — [VALIDATION](../../webui/VALIDATION.md). Развёртывание и изменение эксплуатационной конфигурации требуют отдельного согласования владельца.

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

Перед разрешением запуска заполнить config.py рабочими параметрами локально, проверить webui_server.local.json, создать unit и настроить HTTPS (ниже). Не заменять существующие конфиги примерами. init-admin интерактивно запрашивает логин и пароль (минимум 12 символов), создаёт хеш и случайный ключ; пароля по умолчанию нет. Для смены пароля использовать тот же интерпретатор и `/opt/newsbot/webui_server.py change-password` от newsbot.

### Конфигурация и права

| Путь относительно /opt/newsbot | Владелец | Права на RELEASE |
| --- | --- | --- |
| . | newsbot:newsbot | 750 |
| config.py, webui_server.local.json | newsbot:newsbot | 600 |
| logs/, downloaded_eml/, extracted_images/ | newsbot:newsbot | 700 |
| flags/, .git/, .venv/, cron-newsbot.sh | newsbot:newsbot | 775 |

В новой установке права служебных каталогов могут быть строже; доступ нужен одной учётной записи ОС. Секреты, локальные конфиги, журналы, флаги и временные данные не коммитятся. Не удалять локальные данные через git clean -x/-X. Рабочий config.py использует отдельные присваивания литералов; API сохраняет только разрешённые поля. Ручные изменения применяются к следующему запуску. LOG_DIR задаётся локально; после его ручной смены требуется перезапуск WebUI.

Несекретные параметры webui_server.local.json на RELEASE: host=127.0.0.1, port=8081, threads=12, prefix="", external_https=true, trusted_proxies=["127.0.0.1"], session_hours=8. Не заменять весь JSON этим перечнем — сохранить username, password_hash и session_key. Явный serve --local-http предназначен для локального loopback-стенда, не для эксплуатации.

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

Обновление только до согласованного полного SHA main. Не разрабатывать на RELEASE. До начала проверить отсутствие посторонних изменений tracked-файлов (`git status --short` от newsbot). Если изменены только документы, развёртывание не требуется.

1. Запомнить исходное состояние разрешения расписания. Через WebUI запретить плановые запуски или создать flags/.newsbot.disabled от newsbot; дождаться завершения текущего конвейера. На время обслуживания исключить ручные запуски. Остановить WebUI (`sudo systemctl stop newsbot-webui`), затем убедиться в простое по prelaunch.check_running() и доступности общего lock (команды ниже). Не убивать работающий процесс и не удалять lock-файл.
2. Перезаписать единственную резервную копию двух локальных конфигов и SHA в stable_backup/ (не хранить историю копий). Если запись не удалась, обновление не продолжать.
3. Получить main, проверить согласованный SHA и переключить рабочее дерево на него. Если requirements.txt изменился, обновить зависимости venv; при ошибках не продолжать запуск новой версии.
4. Запустить WebUI. Проверить состояние newsbot-webui, caddy, cron, HTTPS без ошибки сертификата, вход, историю и статус простоя/расписания. Не запускать публикацию только ради проверки. Восстановить исходное разрешение расписания (удалить созданный флаг, только если раньше он отсутствовал), проверить состояние переключателя.
5. Зафиксировать предыдущий/новый SHA и результаты непосредственной проверки. На этом обновление завершено: ждать очередного cron-запуска не требуется.

Команды проверки простоя, копирования и смены кода (выполнять по этапам, после проверки каждого):

```sh
sudo -u newsbot -H sh -c 'cd /opt/newsbot && .venv/bin/python -B -c "import prelaunch; print(prelaunch.check_running())"'
sudo -u newsbot -H flock -n /opt/newsbot/flags/.main.lock true
# Продолжать только при idle и успешном flock.
sudo -u newsbot -H sh -eu -c '
  cd /opt/newsbot
  umask 077
  install -d -m 700 stable_backup
  install -m 600 config.py webui_server.local.json stable_backup/
  git rev-parse HEAD > stable_backup/commit
'
# TARGET задан администратором: согласованный полный SHA.
sudo -u newsbot -H git -C /opt/newsbot fetch origin main
sudo -u newsbot -H git -C /opt/newsbot merge-base --is-ancestor "$TARGET" origin/main
sudo -u newsbot -H git -C /opt/newsbot switch --detach "$TARGET"
# При изменении requirements.txt:
sudo -u newsbot -H /opt/newsbot/.venv/bin/python -m pip install -r /opt/newsbot/requirements.txt
sudo systemctl start newsbot-webui
```

Не удалять flags/, журналы, временные каталоги, локальные конфиги или stable_backup/ при смене кода. Не сбрасывать delayed.publish. Не выполнять force checkout/reset или git clean для устранения конфликтов. При неисправности оставить автоматические запуски выключенными до восстановления.

### Откат

При неудачном обновлении выполнить согласованный откат в том же режиме обслуживания (запрет расписания, простой конвейера, остановленный WebUI). **Не перезаписывать stable_backup при откате.** Взять SHA из stable_backup/commit, проверить наличие коммита и переключить дерево через git switch --detach. Восстановить два конфига через install -m 600 от newsbot из stable_backup/; если менялись зависимости, привести venv к требованиям возвращаемой версии (при необходимости пересоздать только venv). Запустить WebUI, повторить непосредственную проверку и восстановить исходное разрешение расписания. Резервная копия не содержит точного снимка venv, почты, журналов или WordPress; откат кода не откатывает публикации.

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

Журналы конвейера в LOG_DIR (по умолчанию logs), ранние ошибки запуска — stderr терминала/cron или диагностические события WebUI. Настройки хранения: LOG_RETENTION_DAYS=30, LOG_MAX_TOTAL_MB=200 (МиБ), LOG_EMPTY_MIN_AGE_SECONDS=600 по умолчанию. Сам бот очищает архив в начале следующих запусков; не удалять активный журнал или lock. Резервная копия обновления — только один комплект stable_backup; не делать архивов venv или Git при каждом обновлении. При проблемах с публикацией сверить почту, WordPress и журнал; правила восстановления — [pipeline.md](../architecture/pipeline.md).

Офлайн-тесты и приёмка описываются в [VALIDATION](../../webui/VALIDATION.md); не запускать рабочий конвейер вместо диагностических тестов.
