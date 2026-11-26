# Zanevka Newsbot — HTTP API (v1)

Серверная часть WebUI. Все пути относительно настроенного prefix, один origin для HTML, API и SSE. Клиент — [admin.md](admin.md), общая архитектура — [system.md](../architecture/system.md), HTTPS и службы — [deployment.md](../operations/deployment.md).

## Архитектура

webui_server.py обслуживает HTML, ресурсы и API через Flask/Waitress.

| Компонент | Ответственность |
| --- | --- |
| Settings | Безопасный разбор литералов config.py, allowlist, валидация, ревизия, атомарное сохранение |
| BotControl | Статус процесса, запуск отдельного prelaunch, контроль стартового окна, stderr, обнаружение cron |
| LogHub | Общий файловый читатель, снимок истории, replay и очереди SSE |
| ProxyBoundary | Удаление недоверенных forwarding headers, применение доверенного hop и prefix |
| create_app | Маршруты, реестр сессий, авторизация, CSRF, ошибки |

## Доступ и авторизация

- Одна административная учётная запись; локальные credentials хранятся в webui_server.local.json (хеш пароля, ключ сессии). init-admin/change-password — в эксплуатационной инструкции. Рабочего пароля по умолчанию нет.
- GET api/session, POST api/login и GET api/logs/preview доступны до входа. Остальные API требуют действующей сессии. Публичный preview отдаёт тот же ограниченный снимок, что и защищённая history, без отдельной фильтрации. Доступ к серверу ограничивается сетевой конфигурацией.
- Для изменяющих запросов, включая login/logout, нужен X-CSRF-Token из текущей сессии. При успешном входе создаются новый sid и csrf_token.
- Cookie newsbot_session: HttpOnly, SameSite=Strict, Secure в HTTPS, path с учётом prefix. Фиксированный срок по умолчанию 8 часов (1–24), не продлевается запросами. Серверный реестр до 64 сессий. При 10 неудачных попытках входа с одного IP за последние 60 секунд следующая блокируется с 429. Logout, изменение хеша/ключа, истечение и перезапуск API отзывают сессии.
- HTTPS завершается внешним прокси; Waitress по умолчанию loopback:8081. Доверяется только явно заданному непосредственному proxy IP, один hop X-Forwarded-For/Proto/Host/Port; X-Forwarded-Prefix не используется. Prefix сохраняется upstream. HTTPS-режим отклоняет незащищённый запрос; явный локальный HTTP только на loopback, без доверия proxy headers и Secure-cookie.
- Ответы имеют Cache-Control: no-store, X-Content-Type-Options: nosniff, Referrer-Policy: same-origin и X-Frame-Options: DENY. Максимальный размер тела 64 КиБ.

## Маршруты

JSON-тела; для мутаций указан обязательный набор полей. Ошибка API: `{error:{code,message,fields?}}`; при частичном Debug также поля результата.

| Запрос | Тело | Успешный ответ |
| --- | --- | --- |
| GET api/session | — | `{authenticated,csrf_token,expires_at}` |
| POST api/login | `{username,password}` | Представление сессии |
| POST api/logout | `{}` | Представление сессии |
| GET api/status | — | `{process_state,can_run,schedule}` |
| POST api/run | `{}` | 202 `{accepted:true}` |
| GET api/settings | — | `{values,configured_secrets,revision}` |
| PUT api/settings | `{values:{FIELD:value},revision}` | Безопасное представление настроек |
| PUT api/schedule | `{enabled:boolean}` | Представление schedule |
| POST api/debug/reset-flags | `{}` | `{reset,deleted,errors}` |
| GET api/logs/preview | — | Тот же снимок, что history |
| GET api/logs/history | — | `{files,lines,cursor,truncated}` |
| GET api/events?cursor=… | — | SSE; Last-Event-ID приоритетнее cursor |

Основные ошибки: 400 — неверный запрос/неподдерживаемое действие, 401 — нет сессии/неверный вход, 403 — CSRF/граница доступа, 409 — конфликт, 413 — размер запроса, 429 — ограничение входа, 500 — внутренняя ошибка/частичный reset, 503 — недоступность операции или неизвестное состояние.

## Запуск и состояние

process_state: idle / starting / running / unknown. can_run учитывает состояние и паузу 1 секунда после наблюдаемого завершения. API защищает промежуток принятого запуска до появления running; общую блокировку партии удерживает лаунчер. Неизвестное состояние запрещает запуск (503), занятость — 409.

Запуск создаёт отдельный процесс sys.executable -u prelaunch.py с cwd проекта, без shell, не импортирует конвейер для исполнения. На Unix отдельная сессия, на Windows detached process. stdout — DEVNULL, stderr — обычный временный файл flags/.api-stderr-*, не PIPE. Диагностика ограничена первыми 64 КиБ, до 2000 символов на строку, известные секреты маскируются; watcher удаляет временный файл. Остатки после сбоя API можно очистить при простое через Debug. Обычные события бота идут из журналов. Завершение API не завершает конвейер на уровне приложения. 202 подтверждает принятие запроса, а не публикацию. Отдельного поля режима отсрочки нет.

### Расписание

schedule: `{platform,enabled,task_state,expression,timezone,detail}`. platform: Unix/cron или Windows/manual. task_state: present, missing, ambiguous, unknown, unsupported. enabled на Unix определяется отсутствием/наличием disabled независимо от task_state; при неизвестном доступе может быть null. Только present и enabled=true подтверждают обнаруженное разрешённое задание, не исправность службы.

BotControl читает собственный crontab -l, ищет ровно одну команду абсолютного cron-newsbot.sh (прямой вызов, sh или /bin/sh), возвращает expression и только явно предшествующий CRON_TZ, иначе timezone=null. Кэш 30 секунд; API не меняет задания, системный часовой пояс и службы. PUT schedule меняет flags/.newsbot.disabled и обновляет снимок. Windows/manual не поддерживает эту операцию (400). Флаг не влияет на текущую обработку или ручной запуск.

### Debug

POST api/debug/reset-flags только при простое под общей блокировкой очищает все записи flags/, кроме существующего .main.lock. Каталог и inode lock сохраняются, ссылки удаляются без обхода целей. Занятость/старт/занятый lock — 409, неизвестное состояние или небезопасный доступ — 503. Частичный сбой — 500 с error и списками deleted/errors, операция не атомарна. Удаление disabled разрешает cron; delayed.publish и метки пустых журналов также удаляются. Debug не останавливает процесс, не удаляет журналы, не меняет IMAP и не восстанавливает партию. В интерфейсе контрола нет.

## Настройки

Settings разбирает config.py как UTF-8/BOM с отдельными литеральными присваиваниями и комментариями, не исполняет Python. Импорты, вычисляемые значения, повторные/сложные присваивания, отсутствие обязательных полей или symlink отклоняются без изменения файла.

Разрешённые поля определены SETTING_FIELDS:

| Группа | Поля |
| --- | --- |
| Строки IMAP | IMAP_HOST, IMAP_USERNAME, IMAP_MAILBOX, IMAP_TRASH, IMAP_SUBJECT, IMAP_OUTPUT_DIR |
| Строки WordPress | WP_URL, WP_USERNAME, POST_STATUS, GALLERY_SIZE |
| Строки VK | VK_API_VERSION |
| Секреты | IMAP_PASSWORD, WP_APP_PASSWORD, VK_ACCESS_TOKEN |
| Целые числа | IMAP_PORT, IMAP_TIMEOUT, IMAP_RETRY_COUNT, IMAP_RETRY_DELAY, WP_API_TIMEOUT, GALLERY_COLUMNS, VK_API_TIMEOUT |

Числа — JSON integer, диапазоны определены INTEGER_FIELDS. Строки до 4096 символов без CR/LF/NUL; URL WordPress HTTP(S), без userinfo/query/fragment; POST_STATUS draft/publish/pending, GALLERY_SIZE thumbnail/medium/large/full, VK_API_VERSION числовая версия вида 5.199. IMAP_OUTPUT_DIR относительный, без родительского обхода; текущий валидатор исключает первые компоненты flags, webui, gpt_context, probe, .git и logs. DEMO_INTERVAL/DEMO_TIMEZONE и LOG_DIR не редактируются формой.

GET возвращает только несекретные values, булевы configured_secrets и revision (SHA-256 точных байтов). PUT принимает изменённые разрешённые значения и revision; пустые/пропущенные секреты сохраняются, *** отвергается. Неизменённые литералы и комментарии сохраняются. Проверка ревизии и повторная проверка файла перед атомарной заменой; конфликт 409, ошибка записи 503. Настройки действуют со следующего запуска.

## История и SSE

- LogHub одним потоком читает последние десять доступных журналов в хронологическом порядке по байтовым позициям, с накоплением неполного UTF-8, без постоянных открытых handle. Опрос раз в 0,5 секунды.
- Снимок ограничен 10 000 физическими строками и 4 МиБ доступного конца истории. truncated=true означает усечение; строки свыше 64 КиБ ограничиваются с diagnostic. Снимок и его cursor берутся под одним lock.
- Строка: `{identity,file,type,text,level,time,block}`. identity — поколение файла и байтовое смещение; type: header/detail/blank; block — исходный заголовок. text сохраняет табуляцию, timestamp и исходный уровень; клиентское время не подставляется.
- SSE id — epoch:sequence. События line (строка), status (статус), diagnostic (text, level), resync (reason). Replay после cursor из кольца до 2048 событий, затем живой поток. Очередь каждого клиента до 256; отдельных файловых читателей нет.
- Устаревший/чужой cursor, изменение/удаление файла или медленный клиент вызывают resync. Клиент получает новый снимок, дедуплицирует identity, сохраняет показанные/очищенные строки и продолжает с новым cursor. Исчезнувший при чтении/обнаружении журнал исключается без read-error diagnostic; прочие ошибки чтения обрабатываются как ошибки, history/preview при недоступности возвращают 503.
- Heartbeat каждые 10 секунд при простое, проверка действительности сессии примерно каждую секунду, также перед выдачей события; logout/смена пароля/истечение закрывают SSE. Ответ text/event-stream; X-Accel-Buffering: no.

## Ручные запросы

Обычный вход/выход выполняются штатными контролами. После входа, на основной странице, можно проверить GET api/status в DevTools без изменения состояния:

```js
const base = new URL('api/', document.baseURI);
const response = await fetch(new URL('status', base));
console.log(response.status, await response.json());
```

Для Debug только при отдельно согласованном сбросе (удаляет служебные флаги, включая запрет расписания):

```js
{
  const base = new URL('api/', document.baseURI);
  const state = await (await fetch(new URL('session', base))).json();
  const response = await fetch(new URL('debug/reset-flags', base), {
    method: 'POST',
    headers: {'Content-Type':'application/json', 'X-CSRF-Token':state.csrf_token},
    body: '{}'
  });
  console.log(response.status, await response.json());
}
```

URL сохраняют prefix. Не выводить секреты, session cookie или токены в URL/команды/отчёты. Офлайн-проверки — [trial-run.md](../operations/trial-run.md).
