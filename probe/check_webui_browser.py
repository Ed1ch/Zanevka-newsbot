"""Optional browser check against serve_webui_fixture.py only.

Requires an available Playwright Python installation and Chromium; no dependency
is added to production. Never point this diagnostic at a live newsbot instance.
"""
import argparse
import json
from pathlib import Path
import re
import time
from playwright.sync_api import sync_playwright
from check_webui_log_browser import check_log_blocks


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
        previous_journals = set((args.fixture_root / 'logs').glob('*.txt'))
        page.locator('#run-now').click()
        assert page.locator('#run-now').is_disabled()
        second.wait_for_function("document.getElementById('run-now').disabled")
        page.locator('#log-records .message').filter(has_text='OFFLINE BROWSER RUN').last.wait_for()
        deadline = time.monotonic() + 5
        while not (created := set((args.fixture_root / 'logs').glob('*.txt')) - previous_journals):
            assert time.monotonic() < deadline, 'Fixture did not create a new journal'
            page.wait_for_timeout(50)
        assert len(created) == 1, created
        journal = created.pop()
        assert re.fullmatch(r"\d{8}_\d{6}_[0-9a-f]{6}\.txt", journal.name), journal.name
        for panel in (page, second):
            panel.locator('#log-records .file-name').filter(
                has_text=re.compile('^' + re.escape(journal.name) + '$')).first.wait_for()
        # Append externally only after the fixture writer closes its exclusive
        # handle, otherwise its next write can overwrite the injected detail.
        page.locator('#log-records .message').filter(has_text='OFFLINE COMPLETE').last.wait_for()
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
        scroll_token = 'scroll fixture ' + str(time.monotonic_ns()) + ' '
        with journal.open('ab') as stream:
            stream.write(('\t' + '\n\t'.join(scroll_token + str(i) for i in range(120)) + '\n').encode())
        page.locator('#log-records .message').filter(has_text=scroll_token + '119').wait_for()
        def assert_following_tail():
            geometry = page.locator('#log-records').evaluate('''e => {
              const last=e.querySelector('.log-row:last-of-type') || e.querySelectorAll('.log-row')[e.querySelectorAll('.log-row').length-1];
              return {bottom:last.getBoundingClientRect().bottom,viewport:e.getBoundingClientRect().bottom,
                block:last.offsetHeight,height:e.clientHeight,tail:parseFloat(e.lastElementChild.style.height),
                text:last.textContent.slice(0,160)};
            }''')
            assert geometry['block'] > geometry['height'], geometry
            assert 0 <= geometry['viewport'] - geometry['bottom'] < 100, geometry
            assert geometry['tail'] == 0, geometry
        assert_following_tail()
        page.set_viewport_size(dict(width=1100,height=800))
        page.wait_for_function('''() => {
          const output=document.getElementById('log-records');
          const rows=output.querySelectorAll('.log-row');
          return rows[rows.length-1].getBoundingClientRect().bottom <= output.getBoundingClientRect().bottom;
        }''')
        assert_following_tail()
        page.set_viewport_size(dict(width=1440,height=1000))
        page.locator('#log-records').dispatch_event('wheel', {'deltaY':-100})
        page.locator('#log-records').evaluate('e => e.scrollTop = 0')
        manual_token = 'MANUAL SCROLL RETAINED ' + str(time.monotonic_ns())
        with journal.open('ab') as stream: stream.write(('\t' + manual_token + '\n').encode())
        page.locator('#log-records .message').filter(has_text=manual_token).wait_for()
        assert page.locator('#log-records').evaluate('e => e.scrollTop') == 0
        # UI controls retain their existing behavior and DOM.
        page.locator('#tab-mail').click()
        page.locator('[name="IMAP_PORT"]').fill('1993')
        page.locator('[name="IMAP_PASSWORD"]').fill('browser-new-offline-secret')
        page.locator('#save-settings').click()
        page.wait_for_function("!document.getElementById('save-settings').disabled && document.querySelector('[name=IMAP_PASSWORD]').value === ''")
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
        settings = page.evaluate("async () => (await fetch('api/settings')).json()")
        block_checks = check_log_blocks(browser, args.url, settings, context.storage_state())
        assert not errors, errors
        state = page.evaluate("async () => {const s=await(await fetch('api/session')).json();return (await fetch('api/logout',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':s.csrf_token},body:'{}'})).status}")
        assert state == 200
        page.wait_for_url('**/login',timeout=5000)
        print(json.dumps({'browser':browser.version,'live_log_latency_seconds':round(latency,3),
            'page_errors':errors,'block_checks':block_checks,
            'checks':'login/auth/settings/two-panels/SSE/prefix/XSS/scroll/theme/tabs/drag/resize/scale/filter/export/clear/logout'},ensure_ascii=False))
        browser.close()


if __name__ == '__main__':
    main()
