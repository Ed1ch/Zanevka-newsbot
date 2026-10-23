"""Optional browser check against serve_webui_fixture.py only.

Requires an available Playwright Python installation and Chromium; no dependency
is added to production. Never point this diagnostic at a live newsbot instance.
"""
import argparse
import json
from pathlib import Path
import time
from playwright.sync_api import sync_playwright


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
        page.goto(args.url)
        page.wait_for_url("**/login")
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
        assert login_body == page.locator('body').evaluate("e => getComputedStyle(e).backgroundImage")
        second = context.new_page()
        second.goto(args.url)
        second.wait_for_function("!document.getElementById('run-now').disabled")
        page.locator('#run-now').click()
        assert page.locator('#run-now').is_disabled()
        second.wait_for_function("document.getElementById('run-now').disabled")
        page.get_by_text('OFFLINE BROWSER RUN', exact=True).wait_for()
        journal = sorted((args.fixture_root / 'logs').glob('*.txt'))[-1]
        token = 'BROWSER LATENCY ' + str(time.monotonic_ns())
        started = time.monotonic()
        with journal.open('ab') as stream:
            stream.write(('\t' + token + '\n').encode())
            stream.flush()
        page.get_by_text('\t' + token, exact=True).wait_for(timeout=2000)
        latency = time.monotonic() - started
        page.get_by_text('OFFLINE COMPLETE', exact=True).wait_for()
        page.wait_for_function("!document.getElementById('run-now').disabled")
        assert page.evaluate('window.injected') is None
        assert page.locator('#log-records script').count() == 0
        with journal.open('ab') as stream:
            stream.write(('\t' + '\n\t'.join('scroll fixture ' + str(i) for i in range(120)) + '\n').encode())
        page.get_by_text('scroll fixture 119', exact=True).wait_for()
        page.locator('#log-records').dispatch_event('wheel', {'deltaY':-100})
        page.locator('#log-records').evaluate('e => e.scrollTop = 0')
        with journal.open('ab') as stream: stream.write(b'\tMANUAL SCROLL RETAINED\n')
        page.get_by_text('MANUAL SCROLL RETAINED',exact=True).wait_for()
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
        original_theme = page.locator('html').get_attribute('data-newsbot-theme')
        page.locator('#theme-easter-egg').click()
        page.wait_for_function("document.documentElement.dataset.newsbotTheme !== 'original'")
        assert page.locator('html').get_attribute('data-newsbot-theme') != original_theme
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
        state = page.evaluate("async () => {const s=await(await fetch('api/session')).json();return (await fetch('api/logout',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':s.csrf_token},body:'{}'})).status}")
        assert state == 200
        page.wait_for_url('**/login',timeout=5000)
        print(json.dumps({'browser':browser.version,'live_log_latency_seconds':round(latency,3),
            'page_errors':errors,'checks':'login/auth/settings/two-panels/SSE/prefix/XSS/scroll/theme/tabs/drag/resize/scale/filter/export/clear/logout'},ensure_ascii=False))
        browser.close()


if __name__ == '__main__':
    main()
