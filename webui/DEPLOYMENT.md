# Запуск WebUI и API

Flask/Waitress и бот работают на одной машине. Внутренний адрес по умолчанию
`127.0.0.1:8081`, 12 потоков Waitress (запас для 1–3 SSE и обычных запросов).
Внутренний порт, адрес, префикс и число потоков задаются в
`webui_server.local.json`. Занятый порт — ошибка с диагностикой, без автоподбора.
`config.py` остаётся отдельной Python-конфигурацией бота. Оба локальных файла
исключены из Git. Доступ к ним и каталогам `flags/`, журналов и временных данных
нужен только одной учётной записи ОС, запускающей API, бот и cron.

## Первый запуск

Windows 10 / Python 3.13, из каталога проекта (cmd.exe):

```bat
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
if not exist config.py copy config.example.py config.py
.venv\Scripts\python.exe webui_server.py init-admin
.venv\Scripts\python.exe webui_server.py serve --local-http
```

Debian/FreeBSD, из каталога проекта:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
test -e config.py || cp config.example.py config.py
.venv/bin/python webui_server.py init-admin
.venv/bin/python webui_server.py serve --local-http
```

До реального запуска бота заполните `config.py` своими параметрами и отдельно
установите Pandoc. Не заменяйте существующий конфиг примером. Команда
`init-admin` запрашивает имя и пароль через локальный терминал/getpass, создаёт
хеш scrypt и случайный ключ; известного пароля по умолчанию нет. Минимум
12 символов; пароль не передаётся аргументом. На Windows ограничьте ACL двух
локальных конфигов своей учётной записью; на Unix используйте права 600.

Локальная проверка: откройте `http://127.0.0.1:8081/`, войдите на отдельной
странице. `--local-http` явно включает только loopback HTTP, отключает доверие
proxy-заголовкам и Secure-cookie именно для этого запуска. Без него режим
по умолчанию требует внешний HTTPS. Инициализация не устанавливает службы,
прокси, сертификаты, DNS, firewall или задания ОС.

Смена пароля:

```sh
python webui_server.py change-password
```

Используйте тот же интерпретатор venv. Сессии имеют фиксированный срок 8 часов
(настраивается 1–24), HttpOnly, SameSite=Strict и Secure при HTTPS. Logout,
смена пароля/ключа и истечение срока прекращают SSE в течение примерно секунды.
Перезапуск API также отзывает сессии. Перезапуск API не останавливает запущенный
бот: на Unix используется отдельная сессия процесса, на Windows detached process;
stdout направлен в DEVNULL, stderr — в наследуемый обычный временный файл
`flags/.api-stderr-*`, поэтому исчезновение API не ломает PIPE бота. API передаёт
только первые 64 КиБ stderr с маскированием секретов, строки до 2000 символов.
Обычные сообщения поступают только из журнала. После аварии API остаток stderr
можно удалить при простое через Debug. Для запуска вне API ранний stderr остаётся
диагностикой терминала/cron.

## Внешний HTTPS и префикс

В локальном JSON укажите `external_https: true`, `trusted_proxies` со **строгим
списком IP непосредственного прокси** (например, `["127.0.0.1"]`). По умолчанию
список пуст. Заголовки от остальных адресов удаляются. Один доверенный hop
передаёт X-Forwarded-For/Proto/Host/Port; X-Forwarded-Prefix не используется.
При `prefix: "/newsbot"` прокси сохраняет `/newsbot/` в upstream URL.
Для отдельного домена задайте пустой prefix. Страница, API и SSE имеют один origin.
В HTTPS-режиме HTTP без доверенного X-Forwarded-Proto: https отклоняется.
Не публикуйте внутренний порт напрямую в локальную сеть.

Пример Caddy на Windows (Caddy устанавливает администратор):

```caddyfile
newsbot.example.lan {
    tls internal
    redir /newsbot /newsbot/ 308
    handle /newsbot/* {
        reverse_proxy 127.0.0.1:8081 {
            flush_interval -1
            transport http {
                read_timeout 0
                write_timeout 0
            }
        }
    }
}
```

`tls internal` — лишь пример частного CA: доверие сертификату на клиентских
компьютерах настраивает администратор. Для отдельного домена без префикса
уберите redir/handle и поместите reverse_proxy непосредственно в блок сайта.

Пример фрагмента существующего HTTPS VirtualHost Apache на Unix
(нужны mod_proxy, mod_proxy_http, mod_headers и SSL):

```apache
ProxyRequests Off
ProxyPreserveHost On
ProxyTimeout 120
RequestHeader set X-Forwarded-Proto "https"
RequestHeader set X-Forwarded-Port "443"
RedirectMatch 308 ^/newsbot$ /newsbot/
ProxyPass        /newsbot/ http://127.0.0.1:8081/newsbot/ connectiontimeout=5 timeout=120
ProxyPassReverse /newsbot/ http://127.0.0.1:8081/newsbot/
<Location "/newsbot/api/events">
    SetEnv no-gzip 1
    SetEnv proxy-flushall 1
</Location>
```

Сохраните существующие SSL-сертификаты и ограничения доступа LAN в конфигурации
Apache. Apache обычно передаёт chunked SSE сразу; отключите для этого URL
дополнительное сжатие/буферизацию модулей и промежуточных прокси, затем измерьте
доставку строк. Heartbeat идёт каждые 10 секунд; таймауты примеров — 120 секунд.
Для nginx аналогично нужны proxy_buffering off и proxy_read_timeout 120s.
Эти примеры не являются подтверждением работающего HTTPS или прокси.

## Запуск вручную и cron

```sh
python prelaunch.py
```

`python main.py` сохраняет совместимость и делегирует лаунчеру. API запускает
тот же prelaunch отдельным `sys.executable -u`, без shell, с cwd проекта.
Один запрос — один проход. `202` подтверждает принятие запроса, а не публикацию.
Наличие `flags/delayed.publish` выбирает повторный проход для любого источника.
Кнопка сразу выполняет его; подтверждений и отдельного индикатора режима нет.

На Unix администратор добавляет задание **своего пользователя**, например:

```cron
CRON_TZ=Europe/Moscow
0 */2 * * * /absolute/project/cron-newsbot.sh
```

Обёртка использует `.venv/bin/python`; для другого интерпретатора задайте
`NEWSBOT_PYTHON` в окружении cron. Пути абсолютные; исполняемый бит обёртки
должен сохраниться. CRON_TZ поддерживается не всеми реализациями cron, особенно
на FreeBSD: проверьте местную man-страницу. При отсутствии достоверного CRON_TZ
API возвращает timezone=null, не угадывая часовой пояс системы.

API читает только `crontab -l` текущего пользователя, ищет ровно одну команду
данной обёртки (также допускается `/bin/sh /absolute/project/cron-newsbot.sh`).
Ошибка чтения/отсутствие команды/несколько совпадений отображаются честно.
Чтение кэшируется на 30 секунд. Наличие записи не подтверждает работу службы.
Переключатель создаёт/удаляет `flags/.newsbot.disabled`: он влияет только на
следующие cron-запуски; ручной запуск разрешён. API не редактирует crontab/часовой
пояс и не читает чужие задания. На Windows — Windows/manual; переключатель
недоступен, Task Scheduler настраивается вне приложения.

## Ручная проверка API в том же браузере

Все URL относительные и сохраняют `/newsbot/`, если он настроен.
Для ручного входа откройте страницу `/login` (или `/newsbot/login`), заполните
её существующие поля, затем выполните в DevTools (значения не печатаются):

```js
{
  const base = new URL('api/', location.href);
  const state = await (await fetch(new URL('session', base))).json();
  const form = document.getElementById('login-form');
  const response = await fetch(new URL('login', base), {
    method: 'POST',
    headers: {'Content-Type':'application/json', 'X-CSRF-Token':state.csrf_token},
    body: JSON.stringify({username:form.elements.username.value, password:form.elements.password.value})
  });
  form.elements.password.value = '';
  if (response.ok) location.assign(new URL('./', location.href));
}
```

Обычно достаточно штатной кнопки «Войти». После входа ручной Debug:

```js
{
  const base = new URL('api/', location.href);
  const state = await (await fetch(new URL('session', base))).json();
  const response = await fetch(new URL('debug/reset-flags', base), {
    method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf_token}, body:'{}'
  });
  console.log(response.status, await response.json());
}
```

Только при простое Debug держит общую `.main.lock` и удаляет **всё** остальное
содержимое `flags/`, включая `.newsbot.disabled`, свободный `.newsbot.running`,
`delayed.publish`, метки пустых журналов и другие служебные записи. Каталог
и существующий inode `.main.lock` сохраняются. При работе/старте/занятом lock —
409 без удаления, при неизвестном состоянии — 503. Ссылки удаляются как записи,
без обхода их целей. Частичный сбой возвращает 500 и списки deleted/errors.
Удаление disabled разрешает будущий cron. Debug не останавливает процесс,
не удаляет журналы, не меняет IMAP и не восстанавливает партию.

Для logout замените в этом фрагменте `debug/reset-flags` на `logout`.
GET `api/status`, `api/settings`, `api/logs/history` доступны после входа;
пароли/ключи/сессионные cookie не помещайте в URL или команды терминала.

## Настройки и журнал

Редактор config.py поддерживает UTF-8/BOM, отдельные литеральные присваивания
и комментарии, без исполнения Python в API. Импорты, вычисляемые значения,
повторные/сложные присваивания или отсутствующие поля дают понятный отказ;
файл сохраняется. Не требуется переписывать обычный существующий config.py.
Из формы разрешены только соответствующие IMAP/WP/POST_STATUS/GALLERY/VK поля.
Каталог EML при сохранении должен быть относительным, без выхода из проекта
и без служебных каталогов. Остальные переменные/комментарии остаются на месте.
Секреты GET не возвращает; пустое поле сохраняет значение, `***` отвергается.
Изменения применяются к следующему процессу; лаунчер читает исходный config.py
без timestamp-based pyc-кэша. Две панели защищены SHA-256 ревизией, конфликт — 409.

Один журнал создаётся до почты, закрывается после очистки и итога, затем снимается
running и последним общий lock. Пустота определяется явным результатом конвейера,
без предупреждений/ошибок и погашения delayed.publish. После успешного закрытия
создаётся метка в flags. Сам бот удаляет помеченные неизменившиеся файлы в начале
последующих запусков, с возрастом `LOG_EMPTY_MIN_AGE_SECONDS=600` по умолчанию.
Молодой/занятый файл остаётся до следующего раза. Потеря метки (включая Debug)
означает обычное хранение по LOG_DIR/LOG_RETENTION_DAYS/LOG_MAX_TOTAL_MB
(defaults logs/30/200). Пустой повтор с погашением delayed.publish сохраняется.
API журналы не удаляет. Его курсор не участвует в завершении бота.

История — последние 10 файлов; сервер хранит до 10 000 строк/4 МиБ доступного
конца истории, replay до 2048 событий, очередь клиента 256. Переполнение явно
обозначается truncated/resync, а большие строки ограничены 64 КиБ с диагностикой.
Чтение с байтовыми позициями, накоплением неполного UTF-8, без постоянных файловых
handle; новые строки читаются раз в 0,5 секунды одним общим потоком. Удаление
файла/старый курсор/перезапуск требуют явной синхронизации. Уже показанные строки
и очищенное состояние панели сохраняются; новое открытие загружает историю.

## Проверки перед переносом

```sh
python probe/check_logging.py
python probe/check_webui_api.py
python -m compileall -q prelaunch.py main.py log.py webui_server.py probe
node --check webui/index_files/script.js
```

Проверки подменяют почту/VK/WP, используют временные каталоги и реальные отдельные
процессы. `probe/check_batch.py` не менялся и не входит в обязательную проверку
этой интеграции. Опциональная проверка браузера (Playwright/Chromium в отдельной
диагностической среде): запустите `python probe/serve_webui_fixture.py --prefix
/newsbot`, возьмите напечатанный временный root и передайте его
`python probe/check_webui_browser.py --fixture-root ROOT`. Это только офлайн-стенд.

Проверено здесь: Debian 13, Python 3.13.5, Chromium 154, локальный HTTP с префиксом,
проверка доверенных proxy-заголовков в WSGI. Windows 10 и FreeBSD, живой HTTPS
через Caddy/Apache, реальная cron-служба, реальные IMAP/VK/WP/Pandoc здесь не
проверялись. На целевой ОС проверьте зависимости, пути/ACL, msvcrt или flock,
удаление занятого файла, detached-процесс после выхода API, reverse proxy/SSE,
вход/истечение сессии и задержку новой строки (цель до двух секунд).
