(() => {
  'use strict';
  const output = document.getElementById('preview-records');
  const refresh = document.getElementById('refresh-log');
  const panel = output.closest('.log-panel');
  const edge = panel.querySelector('.resize-edge');

  function element(tag, className, text) {
    const node = document.createElement(tag);
    node.className = className;
    node.textContent = text;
    return node;
  }

  function render({files, lines, truncated}) {
    const content = document.createDocumentFragment();
    let index = 0;
    for (const file of files) {
      const heading = element('div', 'preview-session', '');
      heading.append(element('span', 'filename', file), element('span', 'rule', ''));
      content.append(heading);
      while (index < lines.length && lines[index].file === file) {
        const first = lines[index];
        const messages = [];
        do {
          const line = lines[index++];
          const prefix = line.time + ' [' + line.level + '] ';
          messages.push(line.type === 'header' ? line.text.slice(prefix.length) : line.text);
        } while (index < lines.length && lines[index].file === file && lines[index].block === first.block);

        const visible = visibleJournalLines(first.type, first.level, messages);
        if (!visible.length) continue;
        const row = element('div', 'preview-row ' + first.level.toLowerCase(), '');
        const time = element('span', 'time', first.time?.slice(-8) || '');
        time.title = first.time || '';
        row.append(time, element('span', 'level', first.level), element('span', 'message', visible.join('\n')));
        content.append(row);
      }
    }
    if (!lines.length) content.append(element('div', '', 'Журнал пока пуст.'));
    if (truncated) content.append(element('div', '', 'Показана только доступная часть истории.'));
    output.replaceChildren(content);
    output.scrollTop = output.scrollHeight;
  }

  async function load() {
    refresh.disabled = true;
    try {
      const response = await fetch(new URL('api/logs/preview', document.baseURI));
      const data = await response.json();
      if (!response.ok) throw new Error(data.error?.message || 'Ошибка сервера.');
      render(data);
    } catch (failure) {
      output.textContent = 'Не удалось загрузить журнал. ' + failure.message;
    } finally {
      refresh.disabled = false;
    }
  }

  function availableHeight() {
    return Math.round(panel.parentElement.getBoundingClientRect().bottom - panel.getBoundingClientRect().top - 20);
  }

  function resizeHeight(height) {
    panel.style.height = Math.min(availableHeight(), Math.max(300, height)) + 'px';
  }

  const left = panel.querySelector('.resize-left');
  const corner = panel.querySelector('.resize-corner');
  function availableWidth() {
    const app = panel.parentElement;
    // The preview occupies the last grid column in both layouts.
    return parseFloat(getComputedStyle(app).gridTemplateColumns.split(' ').pop());
  }
  function resizeWidth(width) {
    panel.style.setProperty('--preview-width', Math.min(availableWidth(), Math.max(220, width)) + 'px');
  }

  for (const handle of [left, edge, corner]) {
    let startX, startY, startWidth, startHeight;
    handle.addEventListener('pointerdown', event => {
      if (event.button !== 0 || !event.isPrimary) return;
      startX = event.clientX;
      startY = event.clientY;
      startWidth = panel.offsetWidth;
      startHeight = panel.offsetHeight;
      handle.setPointerCapture(event.pointerId);
      panel.classList.add('is-resizing');
    });
    handle.addEventListener('pointermove', event => {
      if (!handle.hasPointerCapture(event.pointerId)) return;
      if (handle !== edge) resizeWidth(startWidth + startX - event.clientX);
      if (handle !== left) resizeHeight(startHeight + event.clientY - startY);
    });
    handle.addEventListener('lostpointercapture', () => panel.classList.remove('is-resizing'));
  }
  left.addEventListener('keydown', event => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    resizeWidth(panel.offsetWidth + (event.key === 'ArrowLeft' ? 20 : -20));
  });
  edge.addEventListener('keydown', event => {
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
    event.preventDefault();
    resizeHeight(panel.offsetHeight + (event.key === 'ArrowDown' ? 20 : -20));
  });
  const sizeObserver = new ResizeObserver(() => {
    const width = availableWidth();
    left.setAttribute('aria-valuemin', Math.min(220, width));
    left.setAttribute('aria-valuemax', width);
    left.setAttribute('aria-valuenow', panel.offsetWidth);
    left.setAttribute('aria-valuetext', panel.offsetWidth + ' px');
    const max = availableHeight();
    edge.setAttribute('aria-valuemin', Math.min(300, max));
    edge.setAttribute('aria-valuemax', max);
    edge.setAttribute('aria-valuenow', panel.offsetHeight);
    edge.setAttribute('aria-valuetext', panel.offsetHeight + ' px');
  });
  sizeObserver.observe(panel);
  sizeObserver.observe(panel.parentElement);
  refresh.addEventListener('click', load);
  load();
})();
