  (() => {
    'use strict';
    const $ = (id) => document.getElementById(id);
    const app = $('app'), form = $('settings-form'), output = $('log-records'), drawer = $('settings-drawer');
    const settings = $('settings'), logBottom = document.querySelector('.log-bottom');
    const panelToggle = $('toggle-panel');
    const tabs = [...document.querySelectorAll('[role="tab"]')];
    const inputs = [...form.querySelectorAll('input[name], select[name]')];
    let defaults = {}, loadedValues = {}, revision = null, csrfToken = '';
    let records = [];
    let removedRecords = null, activeTab = 'mail', collapsed = true;
    const settingsWindow = { x: 0, y: 0, width: 0, height: 0 };
    let windowGesture = null, ignoreTabClick = false, logScale = 100;
    let serverStatus = null, connected = false, requestPending = false, source = null;
    let syncing = false, cursor = null, lastDiagnostic = '';
    const delivered = new Map();
    const apiBase = new URL('api/', document.baseURI);
    const workInputs = inputs.filter(input => !input.name.startsWith('DEMO_'));
    workInputs.forEach(input => { input.disabled = true; input.value = ''; });
    $('save-settings').disabled = true;
    $('reset-settings').disabled = true;
    $('run-now').disabled = true;
    $('schedule-toggle').disabled = true;
    try { localStorage.removeItem('zanevka-console-prototype-v1'); } catch { /* Optional cleanup. */ }
    const dirtyTabs = new Set();
    const levelLabels = { info: 'INFO', success: 'OK', warning: 'WARNING', error: 'ERROR', muted: 'INFO' };
    const logTail = document.createElement('div');
    logTail.className = 'log-scroll-tail';
    logTail.setAttribute('aria-hidden', 'true');
    let followLog = true;

    // The invisible tail allows the last message to reach the top of the viewport.
    function resizeLogTail() {
      const last = logTail.previousElementSibling;
      if (!last || !last.classList.contains('log-row')) return;
      const style = getComputedStyle(output);
      const availableHeight = output.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom);
      logTail.style.height = Math.max(0, availableHeight - last.offsetHeight) + 'px';
    }

    // Follow the last message, not the end of the invisible scroll tail.
    function scrollToLastRecord() {
      const last = logTail.previousElementSibling;
      if (!last || !last.classList.contains('log-row')) return;
      const bottomPadding = parseFloat(getComputedStyle(output).paddingBottom);
      output.scrollTop += (last.getBoundingClientRect().bottom - output.getBoundingClientRect().bottom) / getPageScale() + bottomPadding;
    }

    function setLogFollowing(value) {
      followLog = value;
      if (value) {
        resizeLogTail();
        scrollToLastRecord();
      }
    }

    function renderLog() {
      const filter = $('log-filter').value;
      // Keep physical lines (and the delivery ledger) independent of presentation.
      // A UI event can arrive between a header and its later SSE details.
      const blocks = [], byBlock = new Map();
      for (const record of records) {
        if (!record.identity) { blocks.push(record); continue; }
        const key = JSON.stringify([record.session, record.generation, record.block]);
        let block = byBlock.get(key);
        if (!block) {
          block = { ...record, lines: [] };
          byBlock.set(key, block); blocks.push(block);
        }
        block.lines.push(record.message);
      }
      const visible = blocks.filter(record => {
        if (record.lines) {
          // Blank separators stay in rawText/export, without doubling CSS gaps.
          let start = 0, end = record.lines.length;
          while (start < end && record.lines[start] === '') start++;
          while (end > start && record.lines[end - 1] === '') end--;
          if (start === end) return false;
          record.message = record.lines.slice(start, end).join('\n');
        }
        return filter === 'all' || record.level === filter;
      });
      output.replaceChildren();
      if (!visible.length) {
        const empty = document.createElement('div');
        empty.className = 'log-empty';
        empty.innerHTML = '<svg aria-hidden="true"><use href="#i-log"/></svg><strong></strong><span></span>';
        empty.querySelector('strong').textContent = records.length ? 'Сообщений этого уровня нет' : 'Лог очищен';
        empty.querySelector('span').textContent = records.length ? 'Выбери «Все сообщения», чтобы увидеть весь журнал.' : 'Новые сообщения появятся здесь при следующем запуске.';
        if (!records.length && removedRecords) {
          const undo = document.createElement('button');
          undo.type = 'button'; undo.className = 'inline-undo'; undo.textContent = 'Вернуть записи'; undo.addEventListener('click', restoreLog); empty.append(undo);
        }
        output.append(empty); return;
      }
      let currentSession = '';
      visible.forEach(record => {
        const session = record.session || currentSession || 'WebUI';
        if (session !== currentSession) {
          const heading = document.createElement('div'); heading.className = 'log-session';
          const label = document.createElement('span'); label.className = 'file-name'; label.textContent = session;
          const rule = document.createElement('span'); rule.className = 'session-line';
          heading.append(label, rule); output.append(heading); currentSession = session;
        }
        const row = document.createElement('div'); row.className = 'log-row ' + record.level + (record.emphasis ? ' emphasis' : '');
        const stamp = record.time.match(/^(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d)$/);
        const time = stamp ? stamp[2] + '\n' + stamp[1] : record.time + '\n' + (record.date || '');
        for (const [className, text] of [['time', time], ['level', record.rawLevel || levelLabels[record.level]], ['message', record.message]]) {
          const span = document.createElement('span');
          span.className = className;
          span.textContent = text;

          row.append(span);
        }
        if (record.undo && removedRecords) {
          const undo = document.createElement('button');
          undo.type = 'button'; undo.className = 'inline-undo'; undo.textContent = 'Вернуть записи';
          undo.addEventListener('click', restoreLog); row.lastChild.append(undo);
        }
        output.append(row);
      });
      output.append(logTail);
      resizeLogTail();
    }

    function currentStamp() {
      // Only local UI actions use browser time; bot lines always carry server time.
      const now = new Date(), pad = value => String(value).padStart(2, '0');
      return { session: 'WebUI', time: [now.getHours(), now.getMinutes(), now.getSeconds()].map(pad).join(':'),
        date: now.getFullYear() + '-' + pad(now.getMonth() + 1) + '-' + pad(now.getDate()) };
    }

    let renderQueued = false;
    function paintRecords() {
      const scrollTop = output.scrollTop;
      renderLog();
      if (followLog) scrollToLastRecord();
      else output.scrollTop = scrollTop;
    }

    function appendRecord(record, defer = false) {
      records.push(record);
      if (records.length > 10000) records.splice(0, records.length - 10000);
      if (!defer) { paintRecords(); return; }
      // History and bursts of SSE lines share one paint.
      if (!renderQueued) {
        renderQueued = true;
        requestAnimationFrame(() => { renderQueued = false; paintRecords(); });
      }
    }

    function uiLog(message, level = 'info', undo = false) {
      appendRecord({ ...currentStamp(), level, message: '[UI] ' + message, undo });
    }

    // Any deliberate manual navigation pauses following until the user resumes it.
    output.addEventListener('wheel', event => {
      if (!event.ctrlKey && event.deltaY) setLogFollowing(false);
    }, { passive: true });
    output.addEventListener('touchstart', () => setLogFollowing(false), { passive: true });
    output.addEventListener('pointerdown', event => {
      if (event.clientX >= output.getBoundingClientRect().right - 18) setLogFollowing(false);
    });
    output.addEventListener('keydown', event => {
      if (event.target === output && !event.ctrlKey && !event.altKey && !event.metaKey &&
          ['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)) {
        setLogFollowing(false);
      }
    });
    new ResizeObserver(() => {
      if (!logTail.isConnected) return;
      resizeLogTail();
      if (followLog) scrollToLastRecord();
    }).observe(output);

    function restoreLog() {
      if (!removedRecords) return;
      records = [...removedRecords, ...records.filter(record => !record.undo)];
      removedRecords = null; renderLog(); uiLog('Записи лога восстановлены.');
    }

    function switchTab(name, focus = false) {
      const changed = activeTab !== name;
      activeTab = name;
      tabs.forEach(tab => {
        const selected = tab.dataset.tab === name;
        tab.setAttribute('aria-selected', String(selected)); tab.tabIndex = selected ? 0 : -1;
        $('panel-' + tab.dataset.tab).hidden = !selected;
        if (selected && focus) tab.focus();
      });
      const panel = $('panel-' + name);
      $('settings-heading').textContent = panel.dataset.heading;
      $('settings-description').textContent = panel.dataset.description;
      if (changed) $('settings-scroll').scrollTop = 0;
      if (collapsed) togglePanel();
    }

    function togglePanel() {
      const returnFocus = drawer.contains(document.activeElement);
      finishWindowGesture();
      collapsed = !collapsed;
      app.classList.toggle('panel-collapsed', collapsed);
      settings.classList.toggle('is-open', !collapsed);
      tabs.forEach(tab => tab.setAttribute('aria-selected', String(!collapsed && tab.dataset.tab === activeTab)));
      $('toggle-panel').setAttribute('aria-expanded', String(!collapsed));
      $('toggle-panel').setAttribute('aria-label', collapsed ? 'Показать настройки' : 'Скрыть настройки');
      $('toggle-panel').title = collapsed ? 'Показать настройки' : 'Скрыть настройки';
      $('panel-icon').src = 'index_files/' + (collapsed ? 'minimize-2' : 'maximize-2') + '.svg';
      drawer.inert = collapsed;
      drawer.setAttribute('aria-hidden', String(collapsed));
      if (!collapsed) {
        positionSettingsWindow();
        settings.setAttribute('role', 'dialog');
        settings.setAttribute('aria-modal', 'false');
        settings.setAttribute('aria-labelledby', 'settings-heading');
      } else {
        settings.removeAttribute('role');
        settings.removeAttribute('aria-modal');
        settings.removeAttribute('aria-labelledby');
        if (returnFocus) tabs.find(tab => tab.dataset.tab === activeTab).focus();
      }
    }

    function getPageScale() {
      const zoom = getComputedStyle(document.body).zoom || '1';
      return (parseFloat(zoom) || 1) / (zoom.endsWith('%') ? 100 : 1);
    }

    function positionPanelToggle() {
      panelToggle.style.setProperty('--settings-log-top', $('log-output').getBoundingClientRect().top / getPageScale() + 'px');
    }
    document.body.append(panelToggle);
    positionPanelToggle();
    window.addEventListener('resize', positionPanelToggle);

    function windowOption(name) {
      return parseFloat(getComputedStyle(settings).getPropertyValue('--settings-window-' + name));
    }

    // Geometry uses viewport pixels; only CSS placement is converted for body zoom.
    function paintSettingsWindow() {
      const scale = getPageScale();
      settings.style.setProperty('--settings-page-scale', scale);
      for (const [name, value] of Object.entries(settingsWindow)) {
        const property = { x: 'left', y: 'top', width: 'width', height: 'height' }[name];
        settings.style.setProperty('--settings-' + property, value / scale + 'px');
      }
    }

    function positionSettingsWindow(anchor = true) {
      const margin = windowOption('margin');
      const width = document.documentElement.clientWidth, height = window.innerHeight;
      const bottomEdge = Math.min(height, logBottom.getBoundingClientRect().top);
      settingsWindow.width = Math.max(windowOption('min-width'), Math.min(settingsWindow.width || windowOption('width'), width - 2 * margin));
      settingsWindow.height = Math.max(windowOption('min-height'), Math.min(settingsWindow.height || windowOption('height'), bottomEdge - 2 * margin));
      const right = Math.max(0, width - settingsWindow.width - margin - 120);
      const bottom = Math.max(0, bottomEdge - settingsWindow.height - margin + 10);
      settingsWindow.x = anchor ? right : Math.max(0, Math.min(right, settingsWindow.x));
      settingsWindow.y = anchor ? bottom : Math.max(0, Math.min(bottom, settingsWindow.y));
      paintSettingsWindow();
    }

    function finishWindowGesture(event) {
      if (!windowGesture || (event && event.pointerId !== windowGesture.pointerId)) return;
      const gesture = windowGesture;
      windowGesture = null;
      if (gesture.started) {
        ignoreTabClick = true;
        clickStartedActive = false;
        clickStartedCollapsed = false;
      }
      settings.classList.remove('is-dragging');
      document.body.classList.remove('settings-interacting');
      document.body.style.removeProperty('--settings-cursor');
      if (gesture.handle.hasPointerCapture(gesture.pointerId)) gesture.handle.releasePointerCapture(gesture.pointerId);
    }

    document.addEventListener('pointerdown', () => { ignoreTabClick = false; }, true);
    settings.addEventListener('pointerdown', event => {
      if (collapsed || event.button !== 0 || !event.isPrimary) return;
      const resize = event.target.closest('[data-resize]');
      const tab = event.target.closest('[role="tab"]');
      if (!resize && (!tab || tab.dataset.tab !== activeTab)) return;
      const handle = resize || tab;
      windowGesture = {
        ...settingsWindow, pointerId: event.pointerId, handle,
        startX: event.clientX, startY: event.clientY,
        direction: resize ? resize.dataset.resize : '', started: Boolean(resize)
      };
      handle.setPointerCapture(event.pointerId);
      if (resize) {
        event.preventDefault();
        document.body.style.setProperty('--settings-cursor', getComputedStyle(resize).cursor);
        document.body.classList.add('settings-interacting');
      }
    });
    settings.addEventListener('pointermove', event => {
      const gesture = windowGesture;
      if (!gesture || gesture.pointerId !== event.pointerId) return;
      const dx = event.clientX - gesture.startX, dy = event.clientY - gesture.startY;
      if (!gesture.started) {
        if (Math.hypot(dx, dy) < 5) return;
        gesture.started = true;
        settings.classList.add('is-dragging');
        document.body.classList.add('settings-interacting');
      }
      if (!gesture.direction) {
        // Intentionally unrestricted: reopening returns an offscreen window.
        settingsWindow.x = gesture.x + dx;
        settingsWindow.y = gesture.y + dy;
      } else {
        const direction = gesture.direction;
        if (direction.includes('e') || direction.includes('w')) {
          settingsWindow.width = Math.max(windowOption('min-width'), gesture.width + (direction.includes('w') ? -dx : dx));
          if (direction.includes('w')) settingsWindow.x = gesture.x + gesture.width - settingsWindow.width;
        }
        if (direction.includes('n') || direction.includes('s')) {
          settingsWindow.height = Math.max(windowOption('min-height'), gesture.height + (direction.includes('n') ? -dy : dy));
          if (direction.includes('n')) settingsWindow.y = gesture.y + gesture.height - settingsWindow.height;
        }
      }
      paintSettingsWindow();
    });
    settings.addEventListener('pointerup', finishWindowGesture);
    settings.addEventListener('pointercancel', finishWindowGesture);
    settings.addEventListener('lostpointercapture', finishWindowGesture);
    window.addEventListener('blur', () => finishWindowGesture());
    window.addEventListener('resize', () => { if (!collapsed && !windowGesture) positionSettingsWindow(false); });
    $('close-settings').addEventListener('click', () => { if (!collapsed) togglePanel(); });

    function configStatus(message, state = '') {
      if (message) uiLog(message, state === 'error' ? 'error' : 'info');
    }

    function updateSchedule() {
      const schedule = serverStatus?.schedule;
      const interval = form.elements.DEMO_INTERVAL, timezone = form.elements.DEMO_TIMEZONE;
      interval.disabled = true; timezone.disabled = true;
      const expression = schedule?.expression;
      const match = expression?.match(/^0 \*\/(1|2|4|6|12) \* \* \*$/);
      interval.value = expression === '0 * * * *' ? '1' : match ? match[1] : '';
      timezone.value = schedule?.timezone || '';
      $('cron-expression').textContent = expression || '—';
      $('cron-description').textContent = schedule?.detail || 'Расписание не подтверждено.';
    }

    $('clear-log').addEventListener('click', () => {
      if (!records.length || records.every(record => record.undo)) { uiLog('Лог уже пуст.'); return; }
      removedRecords = records; records = []; uiLog('Лог очищен.', 'info', true);
    });
    $('download-log').addEventListener('click', () => {
      uiLog('Журнал подготовлен к загрузке.');
      const text = records.map(record => record.rawText !== undefined ? record.rawText : record.time + ' [' + levelLabels[record.level] + '] ' + record.message).join('\r\n') + '\r\n';
      const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
      const link = document.createElement('a'); link.href = url; link.download = currentStamp().session;
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
    $('log-filter').addEventListener('change', () => {
      paintRecords();
    });

    function initCustomLogFilter() {
      const nativeSelect = document.getElementById('log-filter');
      const button = document.getElementById('log-filter-button');
      const buttonLabel = button?.querySelector('.log-filter-button-label');
      const menu = document.getElementById('log-filter-menu');
      const options = menu ? Array.from(menu.querySelectorAll('.log-filter-option')) : [];

      if (!nativeSelect || !button || !buttonLabel || !menu || !options.length) return;

      document.body.append(menu);
      function positionMenu() {
        const rect = button.getBoundingClientRect(), scale = getPageScale();
        menu.style.left = rect.left / scale + 'px';
        menu.style.top = rect.bottom / scale + 'px';
      }
      function openMenu() {
        positionMenu();
        menu.hidden = false;
        button.setAttribute('aria-expanded', 'true');
      }

      function closeMenu() {
        menu.hidden = true;
        button.setAttribute('aria-expanded', 'false');
      }

      function setSelected(value, dispatchChange = true) {
        const matchedOption = options.find(option => option.dataset.value === value);
        if (!matchedOption) return;

        options.forEach(option => {
          const selected = option.dataset.value === value;
          option.classList.toggle('is-selected', selected);
          option.setAttribute('aria-selected', selected ? 'true' : 'false');
        });

        buttonLabel.textContent = matchedOption.textContent;
        nativeSelect.value = value;

        if (dispatchChange) {
          nativeSelect.dispatchEvent(new Event('change', { bubbles: true }));
        }
      }

      button.addEventListener('click', event => {
        event.stopPropagation();
        if (menu.hidden) {
          openMenu();
        } else {
          closeMenu();
        }
      });

      options.forEach(option => {
        option.addEventListener('click', event => {
          event.stopPropagation();
          setSelected(option.dataset.value, true);
          closeMenu();
        });
      });

      document.addEventListener('click', event => {
        if (!menu.hidden && !menu.contains(event.target) && !button.contains(event.target)) {
          closeMenu();
        }
      });

      document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && !menu.hidden) {
          closeMenu();
          button.focus();
        }
      });

      nativeSelect.addEventListener('change', () => {
        setSelected(nativeSelect.value, false);
      });
      window.addEventListener('resize', () => { if (!menu.hidden) positionMenu(); });

      setSelected(nativeSelect.value || 'all', false);
    }

    initCustomLogFilter();
    $('toggle-panel').addEventListener('click', () => {
		if (collapsed) {
			switchTab('mail');
		} else {
			togglePanel();
		}
	});
    function updateStatus() {
      const process = connected ? serverStatus?.process_state : 'unknown';
      const busy = requestPending || ['running', 'starting'].includes(process);
      const schedule = serverStatus?.schedule;
      const confirmedSchedule = schedule?.enabled === true && schedule?.task_state === 'present';
      const state = process === 'unknown' || !serverStatus ? 'error' : busy || confirmedSchedule ? 'running' : 'stopped';
      $('running-status').dataset.state = state;
      $('running-status').classList.toggle('is-running', busy);
      $('status-text').textContent = process === 'unknown' || !serverStatus ? 'Ошибка' : schedule?.platform === 'Windows/manual' && !busy ? 'Windows/manual' : {running:'Работает',stopped:'Остановлен'}[state];
      $('running-status').title = process === 'unknown' ? 'Состояние неизвестно; связь с API не подтверждена' : busy ? 'Бот выполняется или запускается' : schedule?.detail || 'Бот не выполняется';
      $('schedule-toggle').setAttribute('aria-checked', String(schedule?.enabled === true));
      $('schedule-toggle').disabled = !connected || !schedule || schedule.platform === 'Windows/manual';
      $('run-now').disabled = !connected || requestPending || !serverStatus?.can_run;
      $('run-now').setAttribute('aria-busy', String(busy));
      updateSchedule();
    }

    function loginRedirect() {
      source?.close();
      location.assign(new URL('login', document.baseURI));
    }

    async function api(path, options = {}) {
      const response = await fetch(new URL(path, apiBase), {
        credentials: 'same-origin', ...options,
        headers: {'Content-Type':'application/json', 'X-CSRF-Token':csrfToken, ...options.headers}
      });
      if (response.status === 401) {
        loginRedirect(); throw Object.assign(new Error('Требуется вход.'), { uiNotice: true });
      }
      const result = await response.json();
      if (!response.ok) {
        const message = result.error?.message;
        throw Object.assign(new Error(message || 'Ошибка API.'), { uiNotice: !message });
      }
      return result;
    }

    function showError(error) {
      // Own API/browser messages stay on the exception, outside the UI journal.
      // Explicit service notices and genuinely missing messages keep their fallback.
      if (error?.uiNotice || !error?.message) uiLog(error?.message || 'Потеряна связь с API.', 'error');
    }

    $('schedule-toggle').addEventListener('click', async () => {
      $('schedule-toggle').disabled = true;
      try {
        await api('schedule', {method:'PUT', body:JSON.stringify({enabled:serverStatus.schedule.enabled !== true})});
        await refreshStatus();
      } catch (error) { showError(error); updateStatus(); }
    });

    $('run-now').addEventListener('click', async () => {
      if ($('run-now').disabled) return;
      requestPending = true; updateStatus(); setLogFollowing(true);
      try {
        await api('run', {method:'POST', body:'{}'});
        uiLog('Запрос запуска принят. Ход обработки — в журнале.');
      } catch (error) { showError(error); }
      finally { requestPending = false; await refreshStatus(); }
    });

    async function refreshStatus() {
      try {
        serverStatus = await api('status'); connected = true; updateStatus();
      } catch (error) {
        connected = false; updateStatus();
        if (lastDiagnostic !== error.message) { showError(error); lastDiagnostic = error.message; }
      }
    }

    function receiveLine(line) {
      const split = line.identity.lastIndexOf(':');
      const generation = line.identity.slice(0, split), offset = Number(line.identity.slice(split + 1));
      if (offset <= (delivered.get(generation) ?? -1)) return;
      delivered.set(generation, offset);
      if (delivered.size > 1024) delivered.delete(delivered.keys().next().value);
      const level = {INFO:'info',OK:'success',WARNING:'warning',ERROR:'error'}[line.level] || 'info';
      const header = line.type === 'header';
      const message = header ? line.text.replace(/^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d \[(INFO|OK|WARNING|ERROR)\] /, '') : line.text.replace(/^\t/, '');
      appendRecord({session:line.file,time:line.time || '',level,rawLevel:line.level,
        identity:line.identity,generation,block:line.block,type:line.type,rawText:line.text,message}, true);
    }

    function connectEvents() {
      source?.close();
      const stream = new URL('events', apiBase); stream.searchParams.set('cursor', cursor);
      source = new EventSource(stream);
      source.addEventListener('open', () => { refreshStatus(); lastDiagnostic = ''; });
      source.addEventListener('line', event => { cursor = event.lastEventId; receiveLine(JSON.parse(event.data)); });
      source.addEventListener('status', event => {
        cursor = event.lastEventId; serverStatus = JSON.parse(event.data); connected = true; updateStatus();
      });
      source.addEventListener('diagnostic', event => {
        cursor = event.lastEventId;
        const data = JSON.parse(event.data); uiLog(data.text, data.level === 'ERROR' ? 'error' : data.level === 'WARNING' ? 'warning' : 'info');
      });
      source.addEventListener('resync', () => {
        source.close();
        uiLog('Доступная история изменилась или соединение прерывалось. Загружается доступная часть журнала; прежние строки сохранены.', 'warning');
        syncHistory().catch(showError);
      });
      source.onerror = async () => {
        connected = false; updateStatus();
        if (!lastDiagnostic) { uiLog('Связь с потоком потеряна; выполняется переподключение.', 'warning'); lastDiagnostic = 'stream'; }
        try { const state = await api('session'); if (!state.authenticated) loginRedirect(); } catch { /* Native EventSource reconnects. */ }
      };
    }

    async function syncHistory() {
      if (syncing) return;
      syncing = true;
      try {
        const history = await api('logs/history');
        for (const line of history.lines) receiveLine(line);
        if (history.truncated) uiLog('История превышает буфер панели; показан доступный конец журналов.', 'warning');
        cursor = history.cursor; connectEvents();
      } finally { syncing = false; }
    }

    function setLogScale(value) {
      const next = Math.max(60, Math.min(200, value));
      if (next === logScale) return;
      logScale = next; output.style.setProperty('--log-scale', String(next / 100));
      $('log-scale').textContent = next + '%'; paintRecords();
    }
	$('reset-log-scale').addEventListener('click', () => {
	  setLogScale(100);
	});
    $('log-output').addEventListener('wheel', event => {
      if (!event.ctrlKey || !event.deltaY) return;
      event.preventDefault(); setLogScale(logScale + (event.deltaY < 0 ? 10 : -10));
    }, { passive: false });
    output.addEventListener('keydown', event => {
      if (!event.ctrlKey || !['+', '=', '-', '_', '0'].includes(event.key)) return;
      event.preventDefault();
      setLogScale(event.key === '0' ? 100 : logScale + (['+', '='].includes(event.key) ? 10 : -10));
    });
	let clickStartedCollapsed = false;
	let clickStartedActive = false;

	tabs.forEach((tab, index) => {

		tab.addEventListener('click', event => {
			if (ignoreTabClick && event.detail) return;
			if (event.detail === 2) return;

			if (event.detail === 1) {
				clickStartedCollapsed = collapsed;
				clickStartedActive =
					!collapsed && activeTab === tab.dataset.tab;
			}

			switchTab(tab.dataset.tab);
		});

		tab.addEventListener('dblclick', () => {
			if (!ignoreTabClick && !clickStartedCollapsed &&
				clickStartedActive &&
				!collapsed &&
				activeTab === tab.dataset.tab) {
				togglePanel();
			}
		});

		tab.addEventListener('keydown', event => {
        ignoreTabClick = false;
        let next;
        if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
        if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = tabs.length - 1;
        if (next !== undefined) { event.preventDefault(); switchTab(tabs[next].dataset.tab, true); }
      });
    });
    function setSecretVisibility(button, reveal) {
      const input = button.previousElementSibling;
      const label = input.name === 'VK_ACCESS_TOKEN' ? 'токен доступа' : input.name === 'WP_APP_PASSWORD' ? 'пароль приложения' : 'пароль';
      input.type = reveal ? 'text' : 'password';
      button.setAttribute('aria-pressed', String(reveal));
      button.setAttribute('aria-label', (reveal ? 'Скрыть ' : 'Показать ') + label);
    }
    document.querySelectorAll('.secret-toggle').forEach(button => {
      button.addEventListener('click', () => {
        setSecretVisibility(button, button.previousElementSibling.type === 'password');
      });
    });
    form.addEventListener('input', event => {
      if (event.target.matches('input, select')) {
        event.target.removeAttribute('aria-invalid');
        if (!dirtyTabs.has(activeTab)) {
          dirtyTabs.add(activeTab);
        }
        updateSchedule();
      }
    });
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (!revision || $('save-settings').disabled) return;
      const invalid = workInputs.find(input => !input.checkValidity());
      if (invalid) {
        const panel = invalid.closest('[role="tabpanel"]'); switchTab(panel.id.replace('panel-', ''));
        invalid.setAttribute('aria-invalid', 'true'); invalid.focus();
        return;
      }
      const values = {};
      for (const input of workInputs) {
        const value = input.type === 'number' ? Number(input.value) : input.value;
        if (input.dataset.secret ? value !== '' : value !== loadedValues[input.name]) values[input.name] = value;
      }
      $('save-settings').disabled = true;
      try {
        const result = await api('settings', {method:'PUT',body:JSON.stringify({values,revision})});
        applySettings(result); dirtyTabs.clear();
        configStatus('Настройки сохранены в config.py; применятся при следующем запуске.');
      } catch (error) { showError(error); }
      finally { $('save-settings').disabled = !revision; }
    });

    function applySettings(result) {
      loadedValues = {...result.values}; defaults = {...result.values}; revision = result.revision;
      workInputs.forEach(input => {
        input.value = input.dataset.secret ? '' : result.values[input.name];
        input.disabled = false;
        if (input.dataset.secret) input.setAttribute('aria-description', result.configured_secrets[input.name] ? 'Значение настроено; пустое поле сохранит его.' : 'Значение не настроено.');
      });
      document.querySelectorAll('.secret-toggle').forEach(button => setSecretVisibility(button, false));
      $('save-settings').disabled = false; $('reset-settings').disabled = false;
    }

    $('reset-settings').addEventListener('click', () => {
      if (!revision) return;
      $('panel-' + activeTab).querySelectorAll('input[name], select[name]').forEach(input => {
        if (input.name.startsWith('DEMO_')) return;
        input.value = input.dataset.secret ? '' : defaults[input.name]; input.removeAttribute('aria-invalid');
      });
      $('panel-' + activeTab).querySelectorAll('.secret-toggle').forEach(button => setSecretVisibility(button, false));
      configStatus('Восстановлены загруженные значения config.py.');
    });

    async function initialize() {
      try {
        const state = await api('session');
        if (!state.authenticated) { loginRedirect(); return; }
        csrfToken = state.csrf_token;
        await refreshStatus();
        try { applySettings(await api('settings')); } catch (error) { showError(error); }
        await syncHistory();
        uiLog('Интервал и часовой пояс доступны только при достоверном чтении cron. Статические подписи вкладки будут уточнены отдельно.');
      } catch (error) {
        connected = false; updateStatus(); showError(error);
        setTimeout(initialize, 3000);
      }
    }
    renderLog(); updateStatus(); initialize();
    setInterval(refreshStatus, 2000);
    window.addEventListener('pagehide', () => source?.close());
  })();
