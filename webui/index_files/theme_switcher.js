// Self-contained theme easter egg; no changes to the main newsbot logic.
(() => {
  'use strict';

  const themes = ['original', 'purple-edge', 'deep-ocean', 'dark-amethyst'];
  const key = 'zanevka-newsbot-theme-v1';
  const root = document.documentElement;
  const trigger = document.getElementById('theme-easter-egg');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let busy = false;

  let previous, current;
  try { previous = localStorage.getItem(key); } catch { /* Storage is optional. */ }
  const choices = themes.filter(theme => theme !== previous);
  changeTheme(choices[Math.floor(Math.random() * choices.length)]);

  function changeTheme(next) {
    current = next;
    root.dataset.newsbotTheme = current;
    try { localStorage.setItem(key, current); } catch { /* Optional preference. */ }
  }

  trigger.addEventListener('click', () => {
    if (busy) return;
    const next = themes[(themes.indexOf(current) + 1) % themes.length];
    if (reducedMotion.matches) { changeTheme(next); return; }

    if (typeof document.startViewTransition === 'function') {
      busy = true;
      try {
        document.startViewTransition(() => changeTheme(next)).finished.finally(() => { busy = false; });
      } catch {
        busy = false;
        changeTheme(next);
      }
      return;
    }

    // When native view transitions are missing, briefly dim the old and new theme.
    busy = true;
    root.classList.add('theme-fade-fallback');
    requestAnimationFrame(() => requestAnimationFrame(() => {
      root.classList.add('theme-fade-dim');
      setTimeout(() => {
        changeTheme(next);
        root.classList.remove('theme-fade-dim');
        setTimeout(() => {
          root.classList.remove('theme-fade-fallback');
          busy = false;
        }, 190);
      }, 170);
    }));
  });
})();
