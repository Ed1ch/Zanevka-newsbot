"""Optional browser check against serve_webui_fixture.py only.

Requires an available Playwright Python installation and Chromium; no dependency
is added to production. Never point this diagnostic at a live newsbot instance.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import time
from playwright.sync_api import sync_playwright


def drag(page, selector, dx, dy):
    box = page.locator(selector).bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx, y + dy, steps=6)
    page.mouse.up()


def check_login_preview(page, url):
    requests = []
    def track_preview(request):
        if request.url.endswith("/logs/preview"):
            requests.append(request.url)

    page.on("request", track_preview)
    page.goto(url)
    page.wait_for_url("**/login")
    output = page.locator("#preview-records")
    output.get_by_text("Журнал пока пуст.").wait_for()
    assert len(requests) == 1
    background = page.request.get(url + "index_files/SHeep_edit.webp")
    assert background.ok and background.headers["content-type"] == "image/webp"
    # Digest of SHeep_edit_new.webp from the supplied artwork archive.
    assert hashlib.sha256(background.body()).hexdigest() == "aff06950609cc7d183b892b27cecb4cc619db3166a3603b0a05296dad65ff453"
    panel = page.locator(".log-panel")
    form = page.locator("#login-form")
    assert panel.bounding_box() == dict(x=1040, y=20, width=380, height=300)
    assert form.bounding_box()["x"] == form.bounding_box()["y"] == 20
    assert form.bounding_box()["width"] == 380
    assert output.evaluate("e => getComputedStyle(e).paddingTop") == "5px"

    files = ["01112026_120000_abcdef.txt", "02112026_120000_abcdef.txt"]
    lines = []
    stamp = "2026-11-02 12:00:00"
    detail = "\tTraceback\n\t    <b>detail</b>"
    for file in files:
        for i in range(24):
            base = dict(file=file, time=stamp, level="ERROR", block=f"{file}:{i}")
            lines.append(dict(base, type="header", text=stamp + " [ERROR] failure"))
            lines.extend(dict(base, type="detail", text=row) for row in detail.splitlines())
    route = "**/api/logs/preview"
    page.route(route, lambda r: r.fulfill(json=dict(files=files, lines=lines, truncated=True)))
    page.get_by_role("button", name="Обновить").click()
    output.get_by_text("Показана только доступная часть истории.").wait_for()
    assert output.locator(".filename").all_text_contents() == files
    assert output.locator(".message").last.text_content() == "failure\n" + detail
    assert output.locator("b").count() == 0
    assert output.evaluate("e => e.scrollHeight - e.scrollTop - e.clientHeight < 2 && e.scrollTop > 0")
    assert output.evaluate("e => getComputedStyle(e).fontSize") == "10px"
    assert page.locator(".log-head").evaluate("e => getComputedStyle(e).backgroundColor") == "rgba(0, 0, 0, 0)"
    assert page.locator("#refresh-log").evaluate("e => getComputedStyle(e).backgroundColor.startsWith('rgb(')")
    edge = page.locator(".resize-edge").bounding_box()
    x, y = edge["x"] + 100, edge["y"] + 6
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y + 120)
    page.mouse.up()
    assert panel.bounding_box() == dict(x=1040, y=20, width=380, height=420)
    drag(page, ".resize-left", -150, 0)
    assert panel.bounding_box() == dict(x=890, y=20, width=530, height=420)
    drag(page, ".resize-corner", -70, 40)
    assert panel.bounding_box() == dict(x=820, y=20, width=600, height=460)
    drag(page, ".resize-left", -780, 0)
    assert panel.bounding_box()["x"] == 412
    page.set_viewport_size(dict(width=390, height=540))
    box = panel.bounding_box()
    assert box["y"] >= form.bounding_box()["y"] + form.bounding_box()["height"]
    assert box["y"] + box["height"] <= 540
    assert box["x"] + box["width"] == 370
    assert output.evaluate("e => e.scrollWidth === e.clientWidth")
    assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
    page.set_viewport_size(dict(width=1440, height=1000))
    assert len(requests) == 2
    page.unroute(route)
    page.route(route, lambda r: r.fulfill(status=503, json={"error": {"message": "fixture error"}}))
    page.get_by_role("button", name="Обновить").click()
    output.get_by_text("fixture error", exact=False).wait_for()
    assert len(requests) == 3
    page.unroute(route)
    page.remove_listener("request", track_preview)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-root", required=True, type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:18081/newsbot/")
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    args = parser.parse_args()
    assert args.fixture_root.name.startswith("newsbot-browser-")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.chromium, headless=True, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width":1440,"height":1000}, accept_downloads=True)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        check_login_preview(page, args.url)
        page.locator('input[name="username"]').fill("browser-admin")
        page.locator('input[name="password"]').fill("wrong")
        page.get_by_role("button", name="Войти").click()
        page.locator("#login-error").filter(has_text="Неверное").wait_for()
        style = page.locator('input[name="username"]').evaluate("e => ({background:getComputedStyle(e).backgroundColor,border:getComputedStyle(e).borderColor,color:getComputedStyle(e).color})")
        login_body = page.locator('body').evaluate("e => getComputedStyle(e).backgroundImage")
        page.locator('input[name="password"]').fill("browser-offline-fixture")
        page.get_by_role("button", name="Войти").click()
        page.wait_for_url(args.url)
        page.wait_for_function("!document.getElementById('save-settings').disabled")
        assert page.locator('[name="IMAP_HOST"]').input_value() == "YOUR_IMAP_SERVER"
        assert page.locator('[name="IMAP_PASSWORD"]').input_value() == ""
        main_style = page.locator('[name="IMAP_HOST"]').evaluate("e => ({background:getComputedStyle(e).backgroundColor,border:getComputedStyle(e).borderColor,color:getComputedStyle(e).color})")
        assert style == main_style, (style, main_style)
        assert "SHeep_edit.webp" in login_body and "radial-gradient" in login_body
        run_button = page.get_by_role("button", name="Запустить сейчас", exact=True)
        assert run_button.get_attribute("title") == "Запустить Newsbot сейчас и опубликовать все новости из почтового ящика"
        assert run_button.locator("use").get_attribute("href") == "#i-file-up"
        assert page.locator(".identity-description").inner_text() == "Бот для размещения новостей на zanevkaorg.ru"
        second = context.new_page()
        second.goto(args.url)
        second.wait_for_function("!document.getElementById('run-now').disabled")
        page.locator('#run-now').click()
        assert page.locator('#run-now').is_disabled()
        second.wait_for_function("document.getElementById('run-now').disabled")
        page.locator('#log-records .message').filter(has_text='OFFLINE BROWSER RUN').wait_for()
        journal = sorted((args.fixture_root / 'logs').glob('*.txt'))[-1]
        assert re.fullmatch(r"\d{8}_\d{6}_[0-9a-f]{6}\.txt", journal.name), journal.name
        for panel in (page, second):
            panel.locator('#log-records .file-name').filter(
                has_text=re.compile('^' + re.escape(journal.name) + '$')).first.wait_for()
        # Append only after the fixture writer closes its exclusive-mode file;
        # concurrent external writes can otherwise be overwritten by that writer.
        page.locator('#log-records .message').filter(has_text='OFFLINE COMPLETE').wait_for()
        page.wait_for_function("!document.getElementById('run-now').disabled")
        token = 'BROWSER LATENCY ' + str(time.monotonic_ns())
        started = time.monotonic()
        with journal.open('ab') as stream:
            stream.write(('\t' + token + '\n').encode())
            stream.flush()
        page.locator('#log-records .message').filter(has_text=token).wait_for(timeout=2000)
        latency = time.monotonic() - started
        assert page.evaluate('window.injected') is None
        assert page.locator('#log-records script').count() == 0
        with journal.open('ab') as stream:
            stream.write(('\t' + '\n\t'.join('scroll fixture ' + str(i) for i in range(120)) + '\n').encode())
        page.locator('#log-records .message').filter(has_text='scroll fixture 119').wait_for()
        page.locator('#log-records').dispatch_event('wheel', {'deltaY':-100})
        page.locator('#log-records').evaluate('e => e.scrollTop = 0')
        with journal.open('ab') as stream: stream.write(b'\tMANUAL SCROLL RETAINED\n')
        page.locator('#log-records .message').filter(has_text='MANUAL SCROLL RETAINED').wait_for()
        assert page.locator('#log-records').evaluate('e => e.scrollTop') == 0
        # UI controls retain their existing behavior and DOM.
        page.locator('#tab-mail').click()
        page.locator('[name="IMAP_PORT"]').fill('1993')
        page.locator('[name="IMAP_PASSWORD"]').fill('browser-new-offline-secret')
        page.locator('#save-settings').click()
        page.get_by_text('[UI] Настройки сохранены в config.py; применятся при следующем запуске.',exact=True).wait_for()
        assert page.locator('[name="IMAP_PASSWORD"]').input_value() == ''
        assert 'browser-new-offline-secret' not in page.evaluate("async () => await (await fetch('api/settings')).text()")
        assert page.evaluate("localStorage.getItem('zanevka-console-prototype-v1')") is None
        before = page.locator('#settings').bounding_box()
        tab = page.locator('#tab-mail').bounding_box()
        page.mouse.move(tab['x']+tab['width']/2,tab['y']+tab['height']/2)
        page.mouse.down(); page.mouse.move(tab['x']+tab['width']/2-40,tab['y']+tab['height']/2-40,steps=6); page.mouse.up()
        after = page.locator('#settings').bounding_box()
        assert after['x'] != before['x'] or after['y'] != before['y']
        handle = page.locator('[data-resize="se"]').bounding_box()
        page.mouse.move(handle['x']+handle['width']/2,handle['y']+handle['height']/2)
        page.mouse.down(); page.mouse.move(handle['x']+handle['width']/2+35,handle['y']+handle['height']/2+25,steps=6); page.mouse.up()
        resized = page.locator('#settings').bounding_box()
        assert resized['width'] > after['width'] or resized['height'] > after['height']
        page.locator('#tab-mail').dblclick()
        page.wait_for_function("document.getElementById('app').classList.contains('panel-collapsed')")
        page.locator('#tab-wordpress').click()
        assert page.locator('#panel-wordpress').is_visible()
        page.locator('#close-settings').click()
        assert not page.locator(".field-key").count()
        page.locator("#toggle-panel").click()
        page.get_by_role("tab", name="Почта", exact=True).click()
        port = page.locator('[name="IMAP_PORT"]')
        port.fill("1994")
        for selector in ("#settings-heading", ".settings-toolbar .panel-description"):
            before = page.locator("#settings").bounding_box()
            drag(page, selector, -18, -12)
            after = page.locator("#settings").bounding_box()
            assert abs(after["x"] - before["x"] + 18) < 1
        for selector in ('[name="IMAP_PORT"]', "#reset-settings", "#settings-scroll", "#tab-vk"):
            before = page.locator("#settings").bounding_box()
            drag(page, selector, 45, 16)
            assert page.locator("#settings").bounding_box() == before
        page.locator("#tab-mail").click()
        port.fill("1994")
        page.locator(".identity-description").click()
        assert page.locator("#toggle-panel").get_attribute("aria-expanded") == "false"
        page.locator("#toggle-panel").click()
        assert port.input_value() == "1994"
        page.locator("#toggle-panel").click()
        assert page.locator("#toggle-panel").get_attribute("aria-expanded") == "false"
        page.emulate_media(reduced_motion="reduce")
        schedule = page.locator("#schedule-toggle")
        for theme in ("purple-edge", "deep-ocean", "dark-amethyst", "original"):
            page.locator("#theme-easter-egg").click()
            assert page.locator("html").get_attribute("data-newsbot-theme") == theme
            for _ in range(2):
                previous = schedule.get_attribute("aria-checked")
                schedule.click()
                page.wait_for_function("old => document.querySelector('#schedule-toggle').ariaChecked !== old", arg=previous)
                track, knob = schedule.locator(".schedule-switch").evaluate("""e => [
                    getComputedStyle(e).backgroundColor, getComputedStyle(e, '::before').backgroundColor]""")
                if previous == "false":
                    assert track != knob
                else:
                    assert track == "rgba(0, 0, 0, 0)"
                assert "rgb(10, 39, 87)" not in (track, knob)

        for width in (1440, 390, 320):
            page.set_viewport_size(dict(width=width, height=1000))
            run = page.locator("#run-now").bounding_box()
            logout = page.locator("#logout").bounding_box()
            status = page.locator("#status-text").bounding_box()
            description = page.locator(".identity-description").bounding_box()
            assert logout["y"] == run["y"] and logout["x"] + logout["width"] <= width
            assert status["y"] + status["height"] <= description["y"] + 1
        page.set_viewport_size(dict(width=1440, height=1000))
        page.locator('#log-output').dispatch_event('wheel', {'ctrlKey':True,'deltaY':-100})
        assert page.locator('#log-scale').text_content() == '110%'
        page.locator('#reset-log-scale').click()
        assert page.locator('#log-scale').text_content() == '100%'
        page.locator('#log-filter').select_option('error')
        assert page.locator('#log-records .log-row.warning').count() == 0
        page.locator('#log-filter').select_option('all')
        with page.expect_download() as download:
            page.locator('#download-log').click()
        text = Path(download.value.path()).read_text(encoding='utf-8')
        assert '\t' + token in text
        assert '[OK] OFFLINE COMPLETE' in text
        page.locator('#clear-log').click()
        assert page.get_by_text(token,exact=False).count() == 0
        # Force an explicit resync while keeping the same document and delivery
        # ledger: deleted files must not restore lines cleared by the user.
        journal.unlink()
        page.wait_for_timeout(1200)
        assert page.get_by_text(token,exact=False).count() == 0
        assert not errors, errors
        logout = page.get_by_role("button", name="Выйти")
        page.route("**/api/logout", lambda route: route.fulfill(status=503, json={"error":{"message":"Ошибка выхода"}}))
        logout.click()
        page.get_by_role("alert").filter(has_text="Ошибка выхода").wait_for()
        assert page.url == args.url and logout.is_enabled()
        page.unroute("**/api/logout")
        old_session = context.storage_state()
        page.locator("#run-now").click()
        page.locator("#log-records .message").filter(has_text="OFFLINE BROWSER RUN").wait_for()
        logout.click()
        page.wait_for_url('**/login',timeout=5000)
        stale = browser.new_context(storage_state=old_session)
        assert stale.request.get(args.url + "api/status").status == 401
        stale.close()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            page.locator("#refresh-log").click()
            if page.locator("#preview-records").get_by_text("OFFLINE COMPLETE").count():
                break
            page.wait_for_timeout(200)
        page.locator("#preview-records").get_by_text("OFFLINE COMPLETE").wait_for()
        print(json.dumps({'browser':browser.version,'live_log_latency_seconds':round(latency,3),
            'page_errors':errors,'checks':'login-preview/auth/settings/two-panels/SSE/prefix/XSS/scroll/theme/tabs/drag/resize/scale/filter/export/clear/logout'},ensure_ascii=False))
        browser.close()


if __name__ == '__main__':
    main()
