"""Targeted browser scenarios, invoked by check_webui_browser.py.

Real UI assets, substituted API/SSE only. No production endpoint is mutated.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlparse


FILE_A = '23102026_141801_57f759.txt'
FILE_B = '24102026_090000_a1b2c3.txt'
STAMP = '2026-10-23 14:18:01'
REMOVED = [
    'Лог уже пуст.', 'Лог очищен.', 'Записи лога восстановлены.',
    'Журнал подготовлен к загрузке.',
    'Настройки сохранены в config.py; применятся при следующем запуске.',
    'Восстановлены загруженные значения config.py.',
    'Запрос запуска принят. Ход обработки — в журнале.',
    'Интервал и часовой пояс доступны только при достоверном чтении cron.',
    'Доступная история изменилась или соединение прерывалось.',
    'Связь с потоком потеряна; выполняется переподключение.',
    'История превышает буфер панели;',
    'Строка журнала превышает лимит 64 КиБ.', 'Чтение журналов восстановлено.',
    'Не удалось прочитать журнал; проверьте доступ.',
    'Ранняя ошибка запуска; config.py недоступен для безопасной диагностики.',
    'Требуется вход.', 'Ошибка API.', 'Потеряна связь с API.',
]


def journal(file, generation, entries):
    lines, offset, block, stamp, level = [], 0, None, None, 'INFO'
    for kind, text in entries:
        identity = f'{file}:{generation}:{offset}'
        if kind == 'header':
            block, stamp = identity, text[:19]
            level = text[21:text.index(']')]
        lines.append(dict(identity=identity, file=file, type=kind, text=text,
                          time=stamp, level=level, block=block))
        offset += len(text.encode('utf-8')) + 1
    return lines


def messages(page):
    return page.locator('#log-records .message').all_text_contents()


def export_text(page):
    with page.expect_download() as download:
        page.locator('#download-log').click()
    return Path(download.value.path()).read_bytes().decode('utf-8')


def assert_silent(page):
    text = '\n'.join(messages(page))
    assert not any('[UI] ' + value in text for value in REMOVED), text


def check_log_blocks(browser, url, settings, storage_state):
    first = journal(FILE_A, 'one', [
        ('header', STAMP + ' [INFO] ПРОВЕРКА INBOX news@example.invalid ...'),
        ('detail', '\tПисем в партии: 1'),
        ('detail', '\tUID партии: 33'),
        ('detail', '\tСкачивание UID 33: попытка 1/3...'),
        ('detail', '\tПисьмо скачано: .\\downloaded_eml\\ЗГП_33.eml'),
        ('detail', '\tUID 33: помечено SEEN до запуска публикации.'),
        ('blank', ''),
        ('header', '2026-10-23 14:18:09 [INFO] ПАРСИНГ VK ссылок'),
        ('detail', '\t<script>window.injected=true</script>'),
        ('header', '2026-10-23 14:18:09 [WARNING] СОПОСТАВЛЕНИЕ DOCX И ИЗОБРАЖЕНИЙ'),
        ('detail', '\t  ✓ изображение.jpg'),
        ('detail', '\tTraceback (most recent call last):'),
        ('detail', '\t    nested call()'),
        ('blank', ''),
        ('detail', '\tURL https://example.invalid/' + 'long/' * 80),
        ('blank', ''),
    ])
    second = journal(FILE_B, 'two', [('header', STAMP + ' [ERROR] ДРУГОЙ ФАЙЛ'),
                                    ('detail', '\tОшибка API.')])
    state = dict(history=dict(files=[FILE_A, FILE_B], lines=first + second,
                              cursor='probe:1', truncated=False),
                 settings=deepcopy(settings), requests=[], run_error=None)
    status = dict(process_state='idle', can_run=True,
                  schedule=dict(platform='Unix', enabled=False, task_state='unknown',
                                expression=None, timezone=None, detail='Fixture'))

    def route_api(route):
        path = urlparse(route.request.url).path.split('/api/', 1)[1]
        state['requests'].append((path, route.request.method, route.request.headers,
                                  route.request.post_data))
        if path == 'run' and state['run_error']:
            code, body = state['run_error']
            if isinstance(body, str):
                route.fulfill(status=code, content_type='application/json', body=body)
            else:
                route.fulfill(status=code, json=body)
            return
        if path == 'settings' and route.request.method == 'PUT':
            data = route.request.post_data_json
            assert data['revision'] == state['settings']['revision']
            state['settings']['values'].update({key: value for key, value in data['values'].items()
                                                if key not in state['settings']['configured_secrets']})
            state['settings']['revision'] = 'probe-revision'
        result = {'session': dict(authenticated=True, csrf_token='probe-csrf'),
                  'status': status, 'settings': state['settings'],
                  'logs/history': state['history'], 'run': dict(accepted=True),
                  'schedule': status['schedule']}[path]
        route.fulfill(json=result)

    context = browser.new_context(viewport=dict(width=1440, height=1000),
                                  timezone_id='Pacific/Honolulu', accept_downloads=True,
                                  storage_state=storage_state)
    context.route('**/api/**', route_api)
    context.route('**/login', lambda route: route.fulfill(body='<title>Fixture login</title>'))
    context.add_init_script("""(() => {
      window.probeStreams = [];
      window.probeCursorReads = [];
      window.EventSource = class extends EventTarget {
        constructor(url) { super(); this.url = String(url); window.probeStreams.push(this); }
        close() { this.closed = true; }
      };
      window.probeEmit = (kind, data, id = 'probe:2') => {
        const stream = window.probeStreams.at(-1);
        if (kind === 'error') return stream.onerror(new Event('error'));
        const event = new MessageEvent(kind, {data: JSON.stringify(data)});
        Object.defineProperty(event, 'lastEventId', {get() { window.probeCursorReads.push(id); return id; }});
        stream.dispatchEvent(event);
      };
      const fetchAPI = window.fetch;
      window.fetch = (...args) => {
        if (new URL(args[0], location).pathname.endsWith('/api/run') && window.probeFailure) {
          const failure = window.probeFailure; window.probeFailure = null;
          return Promise.reject(failure === 'no-message' ? {} : new Error(failure));
        }
        return fetchAPI(...args);
      };
      window.addEventListener('beforeunload', () => {
        if (location.origin !== 'null') sessionStorage.setItem('probe-unload',
          JSON.stringify(Array.from(document.querySelectorAll('#log-records .message'), e => e.textContent)));
      });
    })();""")
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.clock.set_fixed_time(datetime(2026, 10, 28, 0, 1, 2, tzinfo=timezone.utc))

    def ready():
        page.wait_for_function('window.probeStreams.length > 0')
        page.locator('#log-records .message').first.wait_for()

    def emit(kind, data=None, cursor='probe:2'):
        page.evaluate('([kind,data,id]) => window.probeEmit(kind,data,id)', [kind, data, cursor])

    def wait_message(text):
        page.locator('#log-records .message').filter(has_text=text).wait_for()

    def resync():
        count = page.evaluate('window.probeStreams.length')
        emit('resync', dict(reason='cursor_expired'))
        page.wait_for_function('(n) => window.probeStreams.length > n', arg=count)
        page.wait_for_timeout(50)

    page.goto(url)
    ready()
    print('Browser: block rendering and exact physical-line export', flush=True)
    assert len(messages(page)) == 4
    inbox = page.locator('.log-row').filter(has_text='ПРОВЕРКА INBOX')
    assert inbox.count() == inbox.locator('.message').count() == 1
    assert inbox.locator('.message').text_content().split('\n') == [first[0]['text'][27:]] + [line['text'][1:] for line in first[1:6]]
    assert inbox.locator('.time').text_content() == '14:18:01\n2026-10-23'
    assert inbox.locator('.level').all_text_contents() == ['INFO']
    assert page.locator('.file-name').all_text_contents() == [FILE_A, FILE_B]
    warning = page.locator('.log-row.warning .message')
    assert '\n  ✓ изображение.jpg\nTraceback' in warning.text_content()
    assert '\n    nested call()\n\nURL' in warning.text_content()
    assert '\t' not in warning.text_content()
    assert page.evaluate('window.injected') is None
    assert page.locator('#log-records script').count() == 0
    raw = export_text(page)
    assert raw == '\r\n'.join(line['text'] for line in first + second) + '\r\n'

    # The first margin and every subsequent file margin equal two log lines.
    # Narrow screens and large font force genuine filename/URL wrapping.
    geometry_checks = 0
    for width in (390, 1440, 1700):
        page.set_viewport_size(dict(width=width, height=1000))
        for scale in (60, 100, 200):
            page.locator('#reset-log-scale').click()
            for _ in range(abs(scale - 100) // 10):
                page.locator('#log-output').dispatch_event('wheel', dict(ctrlKey=True, deltaY=100 if scale < 100 else -100))
            dimensions = page.evaluate("""() => {
              const output = document.getElementById('log-records'), style = getComputedStyle(output);
              return {line:parseFloat(style.lineHeight), padding:parseFloat(style.paddingTop),
                headings:Array.from(output.querySelectorAll('.log-session'), heading => {
                  const name = heading.firstElementChild.getBoundingClientRect(), rule = heading.lastElementChild.getBoundingClientRect();
                  return {margin:parseFloat(getComputedStyle(heading).marginTop), rule:parseFloat(getComputedStyle(heading.lastElementChild).width),
                    height:parseFloat(getComputedStyle(heading.lastElementChild).height), overlap:name.right > rule.left};
                }), rows:Array.from(output.querySelectorAll('.log-row'), row => {
                  const [time,level,message] = row.children;
                  const measure = time.cloneNode(); measure.style.width='1ch'; row.append(measure);
                  const ch = measure.getBoundingClientRect().width; measure.remove();
                  const t=time.getBoundingClientRect(), l=level.getBoundingClientRect(), m=message.getBoundingClientRect();
                  return {width:t.width,ch,text:time.textContent,overflow:time.scrollWidth > time.clientWidth+1,
                    overlap:t.right > l.left || l.right > m.left, wrap:message.scrollWidth > message.clientWidth+1};
                })};
            }""")
            assert dimensions['padding'] == 0
            assert all(abs(item['margin'] - 2 * dimensions['line']) < .15 and item['rule'] >= 23 and abs(item['height'] - 1) < .05 and not item['overlap'] for item in dimensions['headings']), dimensions
            assert all(abs(item['width'] - 10 * item['ch']) < .2 and len(item['text'].split('\n')) == 2 and not item['overflow'] and not item['overlap'] and not item['wrap'] for item in dimensions['rows']), dimensions
            geometry_checks += 1
    print('Browser: nine width/scale combinations passed', flush=True)
    page.set_viewport_size(dict(width=1440, height=1000))
    page.locator('#reset-log-scale').click()

    # Kept UI types U01–U07/U12/U13/U28 are independent rows, with creation time.
    page.locator('#tab-mail').click()
    secret = page.locator('#panel-mail .secret-toggle')
    secret.click(); secret.click()
    page.locator('[name=IMAP_PORT]').fill('0')
    page.locator('#save-settings').click()
    wait_message('Проверь поле «IMAP_PORT».')
    page.locator('[name=IMAP_PORT]').fill('1994')
    page.locator('#save-settings').click()
    page.wait_for_function("!document.getElementById('save-settings').disabled")
    assert state['settings']['values']['IMAP_PORT'] == 1994
    before = messages(page)
    page.locator('#reset-settings').click()
    assert messages(page) == before
    page.locator('[name=IMAP_PORT]').fill('1995')
    page.locator('#reset-settings').click()
    assert page.locator('[name=IMAP_PORT]').input_value() == '1994'
    page.locator('#tab-wordpress').click()
    page.locator('#close-settings').click()
    kept = ['Открыта вкладка «WordPress».', 'Панель настроек открыта.',
            'Панель настроек свёрнута.', 'Масштаб текста лога: 60%.',
            'Секретное поле показано.', 'Секретное поле скрыто.',
            'Есть несохранённые изменения во вкладке «Почта».', 'Проверь поле «IMAP_PORT».']
    assert all('[UI] ' + text in messages(page) for text in kept)
    ui_row = page.locator('.log-row').filter(has_text='Секретное поле показано.')
    ui_stamp = ui_row.locator('.time').text_content()
    assert ui_stamp == '14:01:02\n2026-10-27'
    page.clock.set_fixed_time(datetime(2026, 10, 29, 1, 2, 3, tzinfo=timezone.utc))
    page.locator('#log-filter').select_option('warning')
    assert page.locator('.log-row').count() == 1
    assert page.locator('.log-row.warning .message').text_content().startswith('СОПОСТАВЛЕНИЕ')
    page.locator('#log-filter').select_option('all')
    assert ui_row.locator('.time').text_content() == ui_stamp
    assert '[UI] Фильтр лога: «Все сообщения».' in messages(page)

    # Append a detail to the historical block after intervening UI actions.
    live = dict(first[5], identity=first[0]['identity'].rsplit(':', 1)[0] + ':9000',
                text='\tLIVE AFTER UI', type='detail')
    emit('line', live); wait_message('LIVE AFTER UI')
    assert inbox.locator('.message').text_content().endswith('\nLIVE AFTER UI')
    emit('line', live)
    assert inbox.locator('.message').text_content().count('LIVE AFTER UI') == 1
    for text, level in [(REMOVED[11], 'WARNING'), (REMOVED[12], 'INFO'),
                        (REMOVED[13], 'WARNING'), (REMOVED[14], 'ERROR'),
                        ('prelaunch.py stderr fixture', 'ERROR')]:
        before = messages(page)
        emit('diagnostic', dict(text=text, level=level), 'probe:diagnostic')
        assert messages(page) == before
    assert page.evaluate("window.probeCursorReads.filter(id => id === 'probe:diagnostic').length") == 5
    before = messages(page)
    emit('error')
    page.wait_for_function("document.getElementById('run-now').disabled")
    assert messages(page) == before
    emit('open')
    page.wait_for_function("!document.getElementById('run-now').disabled")
    state['history']['truncated'] = True
    state['history']['lines'] += [live]
    resync()
    assert messages(page) == before

    # U26 and U27 are silent; U28 retains real API/browser and JSON errors.
    def run():
        page.locator('#run-now').click()
        page.wait_for_function("!document.getElementById('run-now').disabled")

    before = messages(page)
    run()
    assert messages(page) == before
    state['run_error'] = (500, dict(error=dict(code='fixture')))
    run()
    assert messages(page) == before
    state['run_error'] = None
    page.evaluate("window.probeFailure = 'no-message'")
    run()
    assert messages(page) == before
    state['run_error'] = (409, dict(error=dict(code='revision_conflict', message='Fixture revision conflict')))
    run(); wait_message('[UI] Fixture revision conflict')
    state['run_error'] = None
    page.evaluate("window.probeFailure = 'Fixture browser network failure'")
    run(); wait_message('[UI] Fixture browser network failure')
    state['run_error'] = (200, 'fixture-invalid-json')
    before = len(messages(page))
    run()
    assert len(messages(page)) == before + 1
    assert 'JSON' in messages(page)[-1]
    assert_silent(page)
    # A real message identical to a fallback is still U28, never substring-filtered.
    state['run_error'] = (500, dict(error=dict(message='Ошибка API.')))
    run(); wait_message('[UI] Ошибка API.')
    assert all(headers['x-csrf-token'] == 'probe-csrf' for path, method, headers, body in state['requests'] if method in ('PUT', 'POST'))

    # Clear/re-clear are silent; new details do not resurrect the old block.
    page.locator('#clear-log').click(); page.locator('#clear-log').click()
    assert messages(page) == []
    undo = page.get_by_role('button', name='Вернуть записи', exact=True)
    assert undo.count() == 1
    new_detail = dict(live, identity=live['identity'].rsplit(':', 1)[0] + ':9100', text='\tONLY NEW DETAIL')
    emit('line', new_detail); wait_message('ONLY NEW DETAIL')
    assert messages(page) == ['ONLY NEW DETAIL']
    assert undo.count() == 1
    state['history']['lines'] += [new_detail]
    resync()
    assert messages(page) == ['ONLY NEW DETAIL']
    undo.click()
    assert undo.count() == 0
    assert inbox.locator('.message').text_content().endswith('\nLIVE AFTER UI\nONLY NEW DETAIL')
    raw = export_text(page)
    bot_lines = [line for line in raw.split('\r\n')[:-1] if '[UI]' not in line]
    assert bot_lines == [line['text'] for line in first + second + [live, new_detail]]
    assert raw.count('[UI] Ошибка API.') == 1
    assert '14:01:02 [INFO] [UI] Секретное поле показано.' in raw

    # A new generation of the same file cannot merge with the former block.
    replacement = dict(first[0], identity=FILE_A + ':replacement:0', block=first[0]['block'],
                       text=STAMP + ' [INFO] NEW GENERATION')
    emit('line', replacement); wait_message('NEW GENERATION')
    assert page.locator('.log-row').filter(has_text='NEW GENERATION').count() == 1
    assert 'NEW GENERATION' not in inbox.text_content()

    # Truncated snapshot and a >10k physical-line block preserve inherited context.
    many = journal(FILE_A, 'bounded', [('header', STAMP + ' [ERROR] EVICTED HEADER')] +
                   [('detail', '\t  retained ' + str(i)) for i in range(10003)])
    state['history']['lines'] = many
    page.reload(); ready(); wait_message('retained 10002')
    assert len(messages(page)) == 1
    assert messages(page)[0].split('\n')[0] == '  retained 3'
    assert 'EVICTED HEADER' not in messages(page)[0]
    assert page.locator('.time').text_content() == '14:18:01\n2026-10-23'
    assert page.locator('.level').text_content() == 'ERROR'
    assert len(export_text(page).split('\r\n')[:-1]) == 10000
    page.locator('#clear-log').click()
    extra = dict(many[-1], identity=many[-1]['identity'].rsplit(':', 1)[0] + ':999999',
                 text='\t  after bounded clear')
    emit('line', extra); wait_message('after bounded clear')
    page.get_by_role('button', name='Вернуть записи', exact=True).click()
    assert messages(page)[0].startswith('  retained 4\n')
    assert messages(page)[0].endswith('\n  after bounded clear')
    assert len(export_text(page).split('\r\n')[:-1]) == 10000
    state['history']['lines'] = many[-2:]
    page.reload(); ready(); wait_message('retained 10002')
    assert messages(page) == ['  retained 10001\n  retained 10002']
    assert_silent(page)

    # HTTP 401 redirects without U25. Save the outgoing DOM before navigation.
    state['run_error'] = (401, dict(error=dict(message='Требуется вход.')))
    page.locator('#run-now').click()
    page.wait_for_url('**/login')
    outgoing = json.loads(page.evaluate("sessionStorage.getItem('probe-unload')"))
    assert not any('[UI]' in text for text in outgoing), outgoing
    assert not errors, errors
    context.close()
    return dict(geometry_cases=geometry_checks, removed_types=18, kept_types=10,
                scenarios='blocks/raw-export/same-second/file-generation/timestamps/tabs/indent/XSS/filter/clear/restore/partial/buffer/resync/reconnect/diagnostics/401/CSRF/API-browser-errors')
