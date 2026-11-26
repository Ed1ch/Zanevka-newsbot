# HTTP contract (v1)

## Server architecture

`webui_server.py` serves HTML, assets and API through Flask/Waitress.

| Component | Responsibility |
| --- | --- |
| Settings | Parse config.py literals without execution, allowlist, validation, revision check, atomic replacement |
| BotControl | Read prelaunch status, launch a detached prelaunch.py process, protect accepted requests, observe child diagnostics and discover the current user's crontab |
| LogHub | One shared file reader, bounded snapshot, replay and SSE subscriber queues |
| ProxyBoundary | Strip forwarded headers from untrusted immediate peers, apply trusted forwarding and configured application prefix |
| create_app | Routes, session registry, authentication, CSRF and error handling |

The batch pipeline is not imported by the API for execution. The child runs as `sys.executable -u prelaunch.py` with the project directory as cwd. The launcher owns the final process lock. Debug resets flags under that same lock, preserves .main.lock and may report partial failure; it does not stop the bot or modify mail or journals.

GET api/logs/preview is deliberately unauthenticated and returns the same bounded LogHub snapshot as authenticated history, not a separate sanitized preview. HTTPS terminates at an external proxy; Waitress defaults to loopback:8081, explicit local HTTP is restricted to loopback, and the configured prefix is retained in upstream requests. See [deployment](../docs/operations/deployment.md) for configuration and [system architecture](../docs/architecture/system.md) for component boundaries.

All paths below are relative to the configured application prefix, with one
origin for HTML, API and SSE. JSON bodies; `X-CSRF-Token` for every mutation,
including login/logout. API failures are `{error:{code,message,fields?}}`.
Unauthenticated 401, CSRF 403, invalid input 400, conflict 409, unavailable 503.

| Request | Body | Successful response |
| --- | --- | --- |
| GET api/session | — | `{authenticated,csrf_token,expires_at}` (public) |
| POST api/login | `{username,password}` | session representation above |
| POST api/logout | `{}` | session representation above |
| GET api/status | — | `{process_state,can_run,schedule}` |
| POST api/run | `{}` | 202 `{accepted:true}`; one external prelaunch |
| GET api/settings | — | `{values,configured_secrets,revision}` |
| PUT api/settings | `{values:{FIELD:value},revision}` | safe settings representation |
| PUT api/schedule | `{enabled:boolean}` | schedule representation |
| POST api/debug/reset-flags | `{}` | `{reset,deleted,errors}`; partial failure 500 |
| GET api/logs/preview | — | Same bounded snapshot as history (public GET) |
| GET api/logs/history | — | `{files,lines,cursor,truncated}` |
| GET api/events?cursor=… | — | SSE stream; Last-Event-ID takes precedence |

`process_state`: idle/starting/running/unknown. `can_run` includes a one second
cooldown after observed completion. No publication mode or delay indicator.
`schedule`: `{platform,enabled,task_state,expression,timezone,detail}`. `enabled` reflects the absence/presence of the disabled flag on Unix, independently of `task_state`. Only a present matching job with enabled=true confirms that automatic runs are configured and allowed; the cron service itself is not checked.

Settings allow only existing form fields (excluding DEMO_INTERVAL/TIMEZONE).
Numbers are JSON integers. Blank/omitted secrets preserve their previous values.
`revision` is SHA-256 of exact config bytes. Unrecognized or unsafe config syntax
is rejected without executing Python or changing the file.

History is one bounded atomic snapshot of the newest ten available journals,
chronologically ordered. `truncated:true` explicitly means the oldest lines in
that snapshot exceed the documented memory cap. Lines have `{identity,file,type,
text,level,time,block}`; identity is file generation plus byte offset; type is
header/detail/blank; block identifies the original header. Text retains tabs and
raw log levels. No client timestamp is substituted for log timestamps.

Every SSE event has an epoch:sequence `id`. Events: `line`, `status`, `diagnostic`,
`resync`. History captures its cursor under the same lock as the shared reader.
Events after that cursor replay from a bounded ring, then continue live.
An expired/foreign cursor, changed/deleted file or slow client yields `resync`
with `{reason}`; client obtains a new history snapshot, deduplicates identities
already seen, preserves displayed/cleared lines and resumes at its cursor.
A journal removed during discovery or reading is dropped from server history
without a read-error diagnostic; the directory is rediscovered on the next poll
if needed. Other filesystem errors still reach the reader's error handler.
Heartbeat comments every 10 seconds; authentication checked at least every
second, including logout/password change/expiry. One reader polls every 0.5s;
each connection owns only a bounded event queue, never its own file reader.

## Operational details

Sessions have a fixed lifetime (8 hours by default, configurable 1–24), HttpOnly, SameSite=Strict, Secure in HTTPS mode; restarting the API revokes sessions. Logout, password/key changes and expiry terminate SSE within approximately one second. Explicit loopback HTTP disables proxy trust and Secure cookies for that run. Forwarding is trusted only for configured immediate peers; one hop of X-Forwarded-For/Proto/Host/Port is applied, not X-Forwarded-Prefix. A configured prefix is preserved upstream; HTTPS mode rejects untrusted plain HTTP.

The child uses a separate Unix session or Windows detached process; stdout is DEVNULL, stderr is inherited through a temporary regular flags/.api-stderr-* file, not a PIPE. Diagnostics are bounded to the first 64 KiB and 2000 characters per line with secret redaction; the watcher deletes its temporary file. Ordinary bot messages come only from journals. Remaining stderr files after API failure can be cleared by idle Debug.

Settings accepts UTF-8/BOM, individual literal assignments and comments, not imports, computed values, duplicate/complex assignments or missing required fields. IMAP_OUTPUT_DIR is required to be relative without parent traversal; the validator rejects first components flags, webui, gpt_context, probe, .git and logs. Unmodified literals and comments are retained; blank secrets preserve values, *** is rejected. Config changes apply at the next launch. SHA-256 revision conflicts return 409. LOG_DIR is not edited by the form.

History is limited to 10 files, 10,000 physical lines and 4 MiB, replay to 2048 events and client queue to 256. Lines over 64 KiB are bounded with diagnostics. The reader uses byte positions and accumulates incomplete UTF-8, without permanent file handles. Already displayed/cleared lines remain in the browser after resync.

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
