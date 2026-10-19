  (() => {
    'use strict';
    const $ = (id) => document.getElementById(id);
    const app = $('app'), form = $('settings-form'), output = $('log-records'), drawer = $('settings-drawer');
    const settings = $('settings'), logBottom = document.querySelector('.log-bottom');
    const panelToggle = $('toggle-panel');
    const tabs = [...document.querySelectorAll('[role="tab"]')];
    const inputs = [...form.querySelectorAll('input[name], select[name]')];
    const defaults = Object.fromEntries(inputs.map(input => [input.name, input.value]));
    const storageKey = 'zanevka-console-prototype-v1';
    const initialRecords = [
      { session: 'newsbot_2026-10-10.log', time: '02:00:00', level: 'info', message: 'Начало обработки новостной почты.' },
      { time: '02:00:01', level: 'success', message: 'IMAP: подключение установлено. Папка INBOX открыта.' },
      { time: '02:00:02', level: 'info', message: 'Фильтр темы: «ЗГП». Найдено 3 новых письма.' },
      { time: '02:00:03', level: 'info', message: 'Загружено: ЗГП_2026-10-10_001.eml · 248 КБ' },
      { time: '02:00:04', level: 'info', message: 'Извлечены текст, 4 изображения и 1 ссылка на VK.' },
      { time: '02:00:05', level: 'warning', message: 'VK: видеозапись недоступна. Обработка письма продолжается.' },
      { time: '02:00:07', level: 'success', message: 'WordPress: создан черновик «Новости городского поселения».' },
      { time: '02:00:08', level: 'muted', message: 'Временные файлы письма удалены.' },
      { time: '02:00:09', level: 'info', message: 'Загружено: ЗГП_2026-10-10_002.eml · 186 КБ' },
      { time: '02:00:10', level: 'error', message: 'Не найден текст новости. Письмо пропущено, очередь продолжается.' },
      { time: '02:00:11', level: 'info', message: 'Загружено: ЗГП_2026-10-10_003.eml · 314 КБ' },
      { time: '02:00:13', level: 'success', message: 'WordPress: создан черновик «События и объявления».' },
      { time: '02:00:14', level: 'success', message: 'Готово. 3 письма обработано · 2 черновика · 1 письмо пропущено.', emphasis: true },
      { time: '02:00:14', level: 'muted', message: 'Следующий плановый запуск: 04:00 · Europe/Moscow' }
    ];
    let records = initialRecords.map(record => ({...record}));
    let removedRecords = null, paused = false, activeTab = 'mail', collapsed = true;
    const settingsWindow = { x: 0, y: 0, width: 0, height: 0 };
    let windowGesture = null, ignoreTabClick = false;
    let running = false, failed = false, runTimers = [], logScale = 100;
    const dirtyTabs = new Set();
    const levelLabels = { info: 'INFO', success: 'OK', warning: 'WARN', error: 'ERROR', muted: 'INFO' };
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
      output.scrollTop += last.getBoundingClientRect().bottom - output.getBoundingClientRect().bottom + bottomPadding;
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
      const visible = records.filter(record => filter === 'all' || record.level === filter);
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
        const session = record.session || currentSession || 'newsbot_2026-10-10.log';
        if (session !== currentSession) {
          const heading = document.createElement('div'); heading.className = 'log-session';
          const label = document.createElement('span'); label.className = 'file-name'; label.textContent = session;
          const rule = document.createElement('span'); rule.className = 'session-line';
          heading.append(label, rule); output.append(heading); currentSession = session;
        }
        const row = document.createElement('div'); row.className = 'log-row ' + record.level + (record.emphasis ? ' emphasis' : '');
        for (const [className, text] of [['time', record.time], ['level', levelLabels[record.level]], ['message', record.message]]) {
          const span = document.createElement('span');
          span.className = className;
          span.textContent = text;

          if (className === 'message' && record.intro) {
            span.style.color = '#FAFEFF';
          }

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
      const now = new Date(), timeZone = form.elements.DEMO_TIMEZONE.value;
      const parts = new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(now);
      const date = Object.fromEntries(parts.map(part => [part.type, part.value]));
      return {
        session: 'newsbot_' + date.year + '-' + date.month + '-' + date.day + '.log',
        time: new Intl.DateTimeFormat('ru-RU', { timeZone, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(now)
      };
    }

    function appendRecord(record) {
      const scrollTop = output.scrollTop;
      records.push({ ...currentStamp(), ...record });
      renderLog();
      if (followLog) scrollToLastRecord();
      else output.scrollTop = scrollTop;
    }

    function uiLog(message, level = 'info', undo = false) {
      appendRecord({ level, message: '[UI] ' + message, undo });
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
      if (changed) uiLog('Открыта вкладка «' + tabs.find(tab => tab.dataset.tab === name).textContent.trim() + '».');
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
      uiLog(collapsed ? 'Панель настроек свёрнута.' : 'Панель настроек открыта.');
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
      const interval = form.elements.DEMO_INTERVAL.value;
      const labels = {1:'каждый час',2:'каждые 2 часа',4:'каждые 4 часа',6:'каждые 6 часов',12:'каждые 12 часов'};
      $('cron-expression').textContent = interval === '1' ? '0 * * * *' : '0 */' + interval + ' * * *';
      $('cron-description').textContent = 'Запуск ' + labels[interval] + ', в начале часа.';
    }

    $('clear-log').addEventListener('click', () => {
      if (!records.length || records.every(record => record.undo)) { uiLog('Лог уже пуст.'); return; }
      removedRecords = records; records = []; uiLog('Лог очищен.', 'info', true);
    });
    $('download-log').addEventListener('click', () => {
      uiLog('Журнал подготовлен к загрузке.');
      const text = records.map(record => record.time + ' [' + levelLabels[record.level] + '] ' + record.message).join('\r\n') + '\r\n';
      const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
      const link = document.createElement('a'); link.href = url; link.download = currentStamp().session;
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
    $('log-filter').addEventListener('change', () => {
      renderLog();
      uiLog('Фильтр лога: «' + $('log-filter').selectedOptions[0].textContent + '».');
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
      const state = running ? 'running' : failed ? 'error' : paused ? 'stopped' : 'running';
      $('running-status').dataset.state = state;
      $('running-status').classList.toggle('is-running', running);
      $('status-text').textContent = { running: 'Работает', error: 'Ошибка', stopped: 'Остановлен' }[state];
      $('running-status').title = running ? 'Идёт тестовый прогон main.py' : failed ? 'Работа остановлена из-за ошибки' : paused ? 'Автоматические запуски отключены' : 'Автоматические запуски разрешены';
      $('schedule-toggle').setAttribute('aria-checked', String(!paused));
      $('run-now').disabled = running;
      $('run-now').setAttribute('aria-busy', String(running));
    }

    $('schedule-toggle').addEventListener('click', () => {
      paused = !paused;
      updateStatus();
      updateSchedule();
      uiLog(paused ? 'Автоматические запуски отключены.' + (running ? ' Текущая обработка продолжится.' : '') : 'Автоматические запуски включены.');
    });

    function stopRun(errorMessage) {
      runTimers.forEach(clearTimeout); runTimers = [];
      running = false; failed = Boolean(errorMessage); updateStatus();
      if (failed) appendRecord({ level: 'error', message: 'main.py: работа остановлена из-за ошибки. ' + errorMessage });
    }

    $('run-now').addEventListener('click', () => {
      if (running) return;
      running = true; failed = false; updateStatus();
      setLogFollowing(true);
      uiLog('Запрошен тестовый прогон main.py.');
      const steps = initialRecords.slice(0, -1);
      steps.forEach((record, index) => {
        runTimers.push(setTimeout(() => appendRecord({
          level: record.level, emphasis: record.emphasis,
          message: index === 0 ? 'main.py: ' + record.message : record.message
        }), (index + 1) * 650));
      });
      runTimers.push(setTimeout(() => {
        stopRun();
        uiLog('Тестовый прогон main.py завершён. Код выхода: 0.', 'success');
      }, (steps.length + 1) * 650));
    });

    // Test/backend hook; recoverable ERROR entries in the log do not stop a run.
    window.addEventListener('newsbot:error', event => {
      const message = typeof event.detail === 'string' ? event.detail : event.detail && event.detail.message;
      stopRun(message || 'Не удалось завершить обработку.');
    });

    function setLogScale(value) {
      const next = Math.max(60, Math.min(200, value));
      if (next === logScale) return;
      logScale = next; output.style.setProperty('--log-scale', String(next / 100));
      $('log-scale').textContent = next + '%'; uiLog('Масштаб текста лога: ' + next + '%.');
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
        uiLog(button.getAttribute('aria-pressed') === 'true' ? 'Секретное поле показано.' : 'Секретное поле скрыто.');
      });
    });
    form.addEventListener('input', event => {
      if (event.target.matches('input, select')) {
        event.target.removeAttribute('aria-invalid');
        if (!dirtyTabs.has(activeTab)) {
          dirtyTabs.add(activeTab); configStatus('Есть несохранённые изменения во вкладке «' + tabs.find(tab => tab.dataset.tab === activeTab).textContent.trim() + '».');
        }
        updateSchedule();
      }
    });
    form.addEventListener('submit', event => {
      event.preventDefault();
      const invalid = inputs.find(input => !input.checkValidity());
      if (invalid) {
        const panel = invalid.closest('[role="tabpanel"]'); switchTab(panel.id.replace('panel-', ''));
        invalid.setAttribute('aria-invalid', 'true'); invalid.focus();
        configStatus('Проверь поле «' + invalid.closest('.field').querySelector('.field-label').firstChild.textContent.trim() + '».', 'error');
        return;
      }
      const values = Object.fromEntries(inputs.filter(input => !input.dataset.secret).map(input => [input.name, input.value]));
      try {
        localStorage.setItem(storageKey, JSON.stringify(values));
        dirtyTabs.clear();
        configStatus('Настройки макета сохранены в этом браузере. Пароли и токены не сохраняются.', 'saved');
      } catch {
        configStatus('Браузер запретил сохранение. Значения остаются на странице.', 'error');
      }
    });
    $('reset-settings').addEventListener('click', () => {
      const panel = $('panel-' + activeTab);
      panel.querySelectorAll('input[name], select[name]').forEach(input => { input.value = defaults[input.name]; input.removeAttribute('aria-invalid'); });
      panel.querySelectorAll('.secret-toggle').forEach(button => setSecretVisibility(button, false));
      configStatus('Значения вкладки сброшены. Сохрани изменения.'); updateSchedule();
    });
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey) || 'null');
      if (saved && typeof saved === 'object') {
        inputs.filter(input => !input.dataset.secret && Object.hasOwn(saved, input.name)).forEach(input => { input.value = saved[input.name]; });
        configStatus('Загружено из этого браузера', 'saved');
      }
    } catch { /* Defaults remain usable when browser storage is unavailable. */ }
    renderLog(); updateSchedule(); updateStatus();

    function showIntroMessages() {
      const messages = [
		'***************************************************************************************************',
		'Это макет веб-интерфейса для управления ботом, который выкладывает новости на zanevkaorg.ru',
		'Макет предназначен для тестирования интерфейса и отключен от самого бота',
        'Вкладки внизу открывают настройки. Двойной клик по активной вкладке закрывает настройки. За активную вкладку окно можно таскать',
		'Ctrl+колесико мыши меняет масштаб лога',
        'Цветовые темы можно менять кликом по заголовку Zanevka Newsbot',
		'***************************************************************************************************',
      ];

      messages.forEach((message, index) => {
        setTimeout(() => {
          appendRecord({
            level: 'info',
            message,
            intro: true
          });
        }, 3000 * (index + 1));
      });
    }

    if (document.readyState === 'complete') {
      showIntroMessages();
    } else {
      window.addEventListener('load', showIntroMessages, { once: true });
    }
  })();
