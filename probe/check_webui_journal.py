"""Offline Chromium journal scenarios with mocked API/SSE; no bot is launched.

Uses the existing optional Playwright installation, without production changes.
Run: python probe/check_webui_journal.py [--chromium /usr/bin/chromium]
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from webui_server import Settings

URL = "http://newsbot.test/"
STAMP = "2026-10-23 14:18:01"
FILES = ["30092026_141801_aaaaaa.txt", "01102026_141801_bbbbbb.txt", "23102026_141801_cccccc.txt"]
REMOVED = re.compile(r"\[UI\] (?:Открыта вкладка|Панель настроек|Масштаб текста лога|Фильтр лога|Секретное поле|Есть несохранённые изменения|Проверь поле|Загружено из этого браузера|Запрошен тестовый прогон|Тестовый прогон)")


def line(file, offset, block, text, level="INFO", kind="detail", generation="1"):
    prefix = file + ":" + generation
    return dict(identity=f"{prefix}:{offset}", file=file, block=f"{prefix}:{block}",
                time=STAMP, level=level, type=kind, text=text)


def header(file, offset, text, level="INFO", generation="1"):
    return line(file, offset, offset, f"{STAMP} [{level}] {text}", level, "header", generation)


def emit(page, kind, data=None):
    page.evaluate("([kind,data]) => window.streams.at(-1).emit(kind,data)", [kind, data or {}])
    # Flush the production requestAnimationFrame paint, without exposing internals.
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")


def message(page, text):
    return page.locator("#log-records .message").filter(has_text=text)


def export(page):
    with page.expect_download() as download:
        page.locator("#download-log").click()
    text = Path(download.value.path()).read_bytes().decode("utf-8")
    assert "[UI] Журнал подготовлен к загрузке." in text
    return text


def bot_lines(text):
    return [row for row in text.split("\r\n")[:-1] if not re.match(r"\d\d:\d\d:\d\d \[\w+\] \[UI\] ", row)]


def check_layout(page):
    for width in (390, 1440, 1600):
        page.set_viewport_size({"width": width, "height": 1000})
        for scale in (60, 100, 200):
            page.locator("#reset-log-scale").click()
            step = -100 if scale > 100 else 100
            for _ in range(abs(scale - 100) // 10):
                page.locator("#log-output").dispatch_event("wheel", {"ctrlKey": True, "deltaY": step})
            result = page.evaluate("""() => {
              const output=document.getElementById('log-records'), zoom=parseFloat(getComputedStyle(document.body).zoom)||1;
              const errors=[];
              for (const e of output.querySelectorAll('.log-row,.log-session')) {
                const s=getComputedStyle(e), r=e.getBoundingClientRect(), n=e.previousElementSibling;
                const factor=e.classList.contains('log-session')?2:1;
                if (Math.abs(parseFloat(s.marginTop)-factor*parseFloat(s.lineHeight))>0.1) errors.push('margin');
                if (n && Math.abs((r.top-n.getBoundingClientRect().bottom)/zoom-parseFloat(s.marginTop))>1) errors.push('gap');
                if (!e.classList.contains('log-row')) {
                  const rule=e.querySelector('.session-line').getBoundingClientRect();
                  if (rule.width<1 || rule.height<1 || rule.left<e.querySelector('.file-name').getBoundingClientRect().right) errors.push('rule');
                  continue;
                }
                const t=e.querySelector('.time'), l=e.querySelector('.level'), m=e.querySelector('.message');
                const probe=document.createElement('span'); probe.textContent='0000000000'; probe.style.position='absolute'; output.append(probe);
                if (Math.abs(t.getBoundingClientRect().width-probe.getBoundingClientRect().width)>0.2) errors.push('10ch');
                probe.remove();
                if (t.textContent.split('\\n').length!==2 || t.scrollWidth>t.clientWidth+1) errors.push('timestamp');
                if (Math.abs(t.getBoundingClientRect().height/zoom-2*parseFloat(s.lineHeight))>1) errors.push('time height');
                if (t.getBoundingClientRect().right>l.getBoundingClientRect().left || l.getBoundingClientRect().right>m.getBoundingClientRect().left) errors.push('columns');
                if (m.scrollWidth>m.clientWidth+1 || m.getBoundingClientRect().right>r.right+1) errors.push('wrapping');
              }
              return errors;
            }""")
            assert not result, (width, scale, result)
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.locator("#reset-log-scale").click()


def assert_at_bottom(page):
    geometry = """e => {
      const last=[...e.querySelectorAll('.log-row')].at(-1), zoom=parseFloat(getComputedStyle(document.body).zoom)||1;
      return last.getBoundingClientRect().bottom-e.getBoundingClientRect().bottom+parseFloat(getComputedStyle(e).paddingBottom)*zoom;
    }"""
    page.wait_for_function("distance => Math.abs((" + geometry + ")(document.getElementById('log-records'))) < 2", timeout=3000)
    distance = page.locator("#log-records").evaluate(geometry)
    assert abs(distance) < 2, distance


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    parser.add_argument("--screenshot", type=Path, help="Optional preview outside the repository")
    args = parser.parse_args()
    details = ["\tПисем в партии: 1", "\tUID партии: 33", "\t  ✓ изображение.jpg",
               "\t    File traceback.py, line 2", "\t<script>window.injected=true</script> https://example.test/" + "x" * 300]
    history = [line(FILES[0], 100, 0, "\tpartial first block", "WARNING"),
               line(FILES[0], 130, 0, "", "WARNING", "blank"), header(FILES[1], 0, "INBOX")]
    history += [line(FILES[1], i * 100, 0, text) for i, text in enumerate(details, 1)]
    history += [line(FILES[1], 600, 0, "", kind="blank"), header(FILES[1], 700, "SECOND SAME SECOND"),
                header(FILES[2], 0, "OK BLOCK", "OK"), line(FILES[2], 100, 0, "\tok history", "OK"),
                header(FILES[2], 200, "BOT WARNING", "WARNING"), line(FILES[2], 300, 200, "\tfirst warning", "WARNING"),
                line(FILES[2], 400, 200, "", "WARNING", "blank"), line(FILES[2], 500, 200, "\tsecond warning", "WARNING"),
                header(FILES[2], 600, "Открыта вкладка «Почта».", "ERROR")]
    assert not REMOVED.search((ROOT / "webui/index.html").read_text())
    snapshot = {"files": FILES, "lines": history, "cursor": "epoch:10", "truncated": False}
    settings = Settings(ROOT / "config.example.py").get()
    status = {"process_state": "idle", "can_run": True,
              "schedule": {"platform": "Unix", "enabled": False, "task_state": "present", "expression": "0 */2 * * *", "timezone": "UTC", "detail": "offline"}}
    failures, requests, login = [], [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.chromium, headless=True, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, timezone_id="America/Los_Angeles", accept_downloads=True, reduced_motion="reduce")
        context.add_init_script("""window.streams=[];
          window.EventSource=class {
            constructor(url){this.url=String(url);this.listeners={};this.closed=false;window.streams.push(this);}
            addEventListener(kind,fn){this.listeners[kind]=fn;}
            close(){this.closed=true;}
            emit(kind,data){if(kind==='error'){this.onerror();return;}this.listeners[kind]?.({data:JSON.stringify(data),lastEventId:'epoch:99'});}
          };
          const fetchOriginal=window.fetch;
          window.fetch=(...args)=>{
            const fault=window.fetchFault;
            if(fault && String(args[0]).endsWith('/api/run')){window.fetchFault=null;return Promise.reject(new Error(fault.message));}
            return fetchOriginal(...args);
          };""")

        def route_request(route):
            path = urlsplit(route.request.url).path
            if path == "/login":
                login.append(route.request.url)
                route.fulfill(content_type="text/html", body="<p>Offline login redirect</p>")
                return
            if path.startswith("/api/"):
                endpoint = path[5:]
                requests.append((endpoint, route.request.method, route.request.post_data, route.request.headers))
                if endpoint == "run" and failures:
                    code, body = failures.pop(0)
                    route.fulfill(status=code, content_type="application/json", body=json.dumps(body) if body is not None else "not json")
                    return
                data = {"session": {"authenticated": True, "csrf_token": "fixture-csrf"}, "status": status,
                        "settings": settings, "logs/history": snapshot, "run": {"accepted": True}, "schedule": status["schedule"]}[endpoint]
                if endpoint == "settings" and route.request.method == "PUT":
                    body = json.loads(route.request.post_data)
                    assert body["revision"] == settings["revision"]
                    settings["values"].update({key: value for key, value in body["values"].items() if key in settings["values"]})
                    settings["revision"] = "new-revision"
                if route.request.method != "GET":
                    assert route.request.headers["x-csrf-token"] == "fixture-csrf"
                route.fulfill(json=data)
                return
            relative = "index.html" if path == "/" else path.lstrip("/")
            route.fulfill(path=str(ROOT / "webui" / relative))

        context.route(URL + "**", route_request)
        page = context.new_page()
        page.set_default_timeout(10000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.clock.set_fixed_time(datetime(2026, 10, 27, 20, 59, 59, tzinfo=timezone.utc))
        page.goto(URL)
        message(page, "Статические подписи вкладки").wait_for()
        assert page.locator("#log-records .file-name").all_text_contents() == FILES + ["WebUI"]
        assert page.locator("#log-records .log-row").count() == 7
        assert message(page, "INBOX").text_content() == "INBOX\n" + "\n".join(text[1:] for text in details)
        assert message(page, "INBOX").locator("..").locator(".time").text_content() == "14:18:01\n2026-10-23"
        assert message(page, "partial first block").text_content() == "partial first block"
        assert message(page, "BOT WARNING").text_content() == "BOT WARNING\nfirst warning\n\nsecond warning"
        assert page.locator("#log-records script").count() == 0 and page.evaluate("window.injected") is None
        assert page.locator("#log-records .log-row.error").count() == 1
        ui_stamp = message(page, "Статические подписи вкладки").locator("..").locator(".time").text_content()
        assert ui_stamp == "13:59:59\n2026-10-27", ui_stamp
        page.clock.set_fixed_time(datetime(2026, 10, 29, 1, tzinfo=timezone.utc))

        # Live details join the correct history block, across independent UI events.
        live = line(FILES[2], 700, 0, "\tlate OK detail", "OK")
        emit(page, "line", live); history.append(live)
        emit(page, "diagnostic", {"text": "interleaved UI", "level": "INFO"})
        late = line(FILES[2], 800, 0, "\t  ✓ second live detail", "OK")
        emit(page, "line", late); history.append(late)
        emit(page, "line", late)
        assert message(page, "OK BLOCK").text_content() == "OK BLOCK\nok history\nlate OK detail\n  ✓ second live detail"
        assert message(page, "OK BLOCK").locator("..").locator("span").count() == 3
        assert message(page, "Статические подписи вкладки").locator("..").locator(".time").text_content() == ui_stamp
        assert bot_lines(export(page)) == [item["text"] for item in history]

        for theme in ("original", "purple-edge", "deep-ocean", "dark-amethyst"):
            page.evaluate("theme => document.documentElement.dataset.newsbotTheme=theme", theme)
            colors = page.evaluate("""() => Object.fromEntries(['success','warning','error','info'].map(level=>{
              const row=document.querySelector('.log-row.'+level);return [level,['.level','.message','.time'].map(sel=>getComputedStyle(row.querySelector(sel)).color)];}))""")
            assert colors["success"][:2] == ["rgb(157, 232, 186)"] * 2, colors
            assert colors["success"][2] == colors["info"][2]
            assert colors["warning"][0] != colors["error"][0] != colors["info"][0]
        page.evaluate("document.documentElement.dataset.newsbotTheme='original'")
        check_layout(page)
        if args.screenshot:
            page.locator("#log-records").evaluate("e=>e.scrollTop=0")
            page.screenshot(path=str(args.screenshot))
        print("journal: layout, grouping, colors and raw export passed", flush=True)
        # Very long names wrap while retaining a visible horizontal rule.
        label = page.locator(".file-name").first
        label.evaluate("e => e.textContent=e.textContent.repeat(8)")
        assert label.evaluate("e=>e.getBoundingClientRect().right <= e.nextElementSibling.getBoundingClientRect().left")
        page.locator("#log-filter").select_option("warning")
        assert page.locator("#log-records .log-row").count() == 2
        assert message(page, "OK BLOCK").count() == 0
        assert "second warning" in message(page, "BOT WARNING").text_content()
        page.locator("#log-filter").select_option("all")

        # Silent controls still change their state; validation blocks the mutation.
        page.locator("#tab-mail").click()
        page.locator(".secret-toggle").first.click()
        assert page.locator("[name=IMAP_PASSWORD]").get_attribute("type") == "text"
        page.locator(".secret-toggle").first.click()
        page.locator("[name=IMAP_PORT]").fill("0")
        count = len(requests)
        page.locator("#save-settings").click()
        assert page.locator("[name=IMAP_PORT]").get_attribute("aria-invalid") == "true"
        assert not any(endpoint == "settings" and method == "PUT" for endpoint, method, *_ in requests[count:])
        page.locator("[name=IMAP_PORT]").fill("1993")
        page.locator("[name=IMAP_PASSWORD]").fill("offline-secret")
        page.locator("#save-settings").click()
        message(page, "Настройки сохранены в config.py;").wait_for()
        assert page.locator("[name=IMAP_PASSWORD]").input_value() == ""
        page.locator("[name=IMAP_PORT]").fill("123")
        page.locator("#reset-settings").click()
        assert page.locator("[name=IMAP_PORT]").input_value() == "1993"
        message(page, "Восстановлены загруженные значения").wait_for()
        page.locator("#close-settings").click()
        assert not REMOVED.search(page.locator("#log-records").text_content())
        assert not REMOVED.search(export(page))

        # Own messages never become fallback, even when their text equals a notice.
        before = page.locator("#log-records .log-row.error").count()
        for body in ({"error": {"message": "private API conflict"}}, {"error": {"message": "Ошибка API."}}, {"error": {"message": "Требуется вход."}}, None):
            failures.append((409, body))
            page.locator("#run-now").click()
            page.wait_for_function("!document.getElementById('run-now').disabled")
            assert page.locator("#log-records .log-row.error").count() == before
        page.evaluate("window.fetchFault={message:'Потеряна связь с API.'}")
        page.locator("#run-now").click()
        page.wait_for_function("!document.getElementById('run-now').disabled")
        assert page.locator("#log-records .log-row.error").count() == before
        failures.append((503, {"error": {}}))
        page.locator("#run-now").click()
        message(page, "[UI] Ошибка API.").wait_for()
        page.wait_for_function("!document.getElementById('run-now').disabled")
        page.evaluate("window.fetchFault={message:''}")
        page.locator("#run-now").click()
        message(page, "[UI] Потеряна связь с API.").wait_for()
        page.wait_for_function("!document.getElementById('run-now').disabled")
        page.locator("#run-now").click()
        message(page, "Запрос запуска принят.").wait_for()
        page.wait_for_function("!document.getElementById('run-now').disabled")
        assert "private API conflict" not in export(page)

        for text, level in [("Строка журнала превышает лимит 64 КиБ.", "WARNING"), ("Чтение журналов восстановлено.", "INFO"), ("Не удалось прочитать журнал; проверьте доступ.", "WARNING"), ("Ранняя ошибка запуска; config.py недоступен для безопасной диагностики.", "ERROR"), ("early stderr own text", "ERROR")]:
            emit(page, "diagnostic", {"text": text, "level": level})
            assert message(page, "[UI] " + text).count() == 1
        emit(page, "error")
        message(page, "Связь с потоком потеряна;").wait_for()
        emit(page, "open")
        snapshot["truncated"] = True
        emit(page, "resync")
        message(page, "История превышает буфер панели;").wait_for()
        assert message(page, "Доступная история изменилась или соединение прерывалось.").count() == 1
        assert page.evaluate("window.streams.at(-1).url").endswith("cursor=epoch%3A10")
        assert message(page, "OK BLOCK").text_content().count("late OK detail") == 1
        assert page.evaluate("window.streams.length") == 2

        page.locator("#clear-log").click()
        message(page, "[UI] Лог очищен.").wait_for()
        page.locator("#clear-log").click()
        message(page, "[UI] Лог уже пуст.").wait_for()
        continuation = line(FILES[2], 900, 0, "\tcontinued after clear", "OK")
        emit(page, "line", continuation); history.append(continuation)
        assert message(page, "OK BLOCK").count() == 0
        assert message(page, "continued after clear").text_content() == "continued after clear"
        assert page.get_by_role("button", name="Вернуть записи").count() == 1
        emit(page, "resync")
        page.wait_for_function("window.streams.length===3")
        assert message(page, "OK BLOCK").count() == 0
        assert bot_lines(export(page)) == [continuation["text"]]
        page.get_by_role("button", name="Вернуть записи").click()
        message(page, "Записи лога восстановлены.").wait_for()
        assert page.get_by_role("button", name="Вернуть записи").count() == 0
        assert message(page, "OK BLOCK").text_content().count("continued after clear") == 1
        assert bot_lines(export(page)) == [item["text"] for item in history]

        print("journal: notices, errors, controls and clear/restore passed", flush=True)
        # A new file generation has the same filename/timestamp, a separate block.
        replacement = header(FILES[2], 0, "REPLACED GENERATION", "OK", generation="2")
        emit(page, "line", replacement); history.append(replacement)
        assert message(page, "REPLACED GENERATION").text_content() == "REPLACED GENERATION"
        assert message(page, "OK BLOCK").count() == 1

        # One huge block must still evict physical lines at the existing 10k cap.
        many = [line(FILES[2], 1000+i, 0, f"\tbuffer detail {i}", "OK", generation="2") for i in range(10005)]
        page.evaluate("lines=>{for(const data of lines)window.streams.at(-1).emit('line',data)}", many)
        message(page, "buffer detail 10004").wait_for()
        assert message(page, "REPLACED GENERATION").count() == 0
        assert_at_bottom(page)
        retained = bot_lines(export(page))
        assert retained == [item["text"] for item in many[-9999:]]  # U11 consumes one physical slot.
        assert message(page, "buffer detail 10004").locator("..").locator(".time").text_content() == "14:18:01\n2026-10-23"
        page.locator("#log-records").dispatch_event("wheel", {"deltaY": -100})
        page.locator("#log-records").evaluate("e=>e.scrollTop=100")
        emit(page, "line", line(FILES[2], 12000, 0, "\tmanual position retained", "OK", generation="2"))
        assert page.locator("#log-records").evaluate("e=>e.scrollTop") == 100
        page.locator("#run-now").click()
        page.wait_for_function("!document.getElementById('run-now').disabled")
        emit(page, "line", line(FILES[2], 13000, 0, "\ttall block actual bottom", "OK", generation="2"))
        assert_at_bottom(page)
        page.set_viewport_size({"width": 900, "height": 700})
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        assert_at_bottom(page)

        page.locator("#log-records").dispatch_event("wheel", {"deltaY": -100})
        page.locator("#log-records").evaluate("e=>e.scrollTop=e.scrollHeight")
        top_gap = page.locator("#log-records").evaluate("e=>[...e.querySelectorAll('.log-row')].at(-1).getBoundingClientRect().top-e.getBoundingClientRect().top")
        assert abs(top_gap) < 2, top_gap
        print("journal: bounded buffer, scroll, tail and resize passed", flush=True)
        # A fresh document gets history normally, even after the previous clear.
        fresh = context.new_page(); fresh.goto(URL)
        message(fresh, "OK BLOCK").wait_for()
        assert not errors, errors
        fresh.close()
        # Observe the notice before the real navigation destroys this document.
        notices = []
        page.on("console", lambda item: notices.append(item.text) if item.text.startswith("journal-notice:") else None)
        page.evaluate("""() => {
          const append=Element.prototype.append;
          Element.prototype.append=function(...nodes){
            const result=append.apply(this,nodes);
            if(this.id==='log-records' && nodes.some(node=>node.textContent?.includes('[UI] Требуется вход.')))
              console.debug('journal-notice:'+window.streams.at(-1).closed);
            return result;
          };
        }""")
        failures.append((401, {"error": {"message": "irrelevant 401 body"}}))
        page.locator("#run-now").click()
        page.wait_for_url(URL + "login")
        assert login and "journal-notice:true" in notices, {"login": login, "notices": notices}
        assert not errors, errors
        print(json.dumps({"browser": browser.version, "os": sys.platform, "checks": "blocks/history/SSE/dedupe/UI-interleave/raw-export/10ch/spacing/60-100-200/narrow-wide/themes/OK/partial/buffer/generations/clear-restore/resync/reopen/controls/validation/U01-U28/H01-H03/401/CSRF/XSS", "page_errors": errors}, ensure_ascii=False))
        browser.close()


if __name__ == "__main__":
    main()
