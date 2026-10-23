# HTTP contract (v1)

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
| GET api/logs/history | — | `{files,lines,cursor,truncated}` |
| GET api/events?cursor=… | — | SSE stream; Last-Event-ID takes precedence |

`process_state`: idle/starting/running/unknown. `can_run` includes a one second
cooldown after observed completion. No publication mode or delay indicator.
`schedule`: `{platform,enabled,task_state,expression,timezone,detail}`. Unknown
task discovery is never represented as a working enabled schedule.

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
Heartbeat comments every 10 seconds; authentication checked at least every
second, including logout/password change/expiry. One reader polls every 0.5s;
each connection owns only a bounded event queue, never its own file reader.
