(() => {
  'use strict';
  const form = document.getElementById('login-form');
  const button = form.querySelector('button');
  const error = document.getElementById('login-error');
  const api = new URL('api/', document.baseURI);
  let csrf = '';
  async function session() {
    try {
      const response = await fetch(new URL('session', api), {credentials:'same-origin'});
      const state = await response.json();
      if (!response.ok) throw new Error(state.error?.message || 'Сессия недоступна.');
      if (state.authenticated) { location.replace(new URL('./', document.baseURI)); return; }
      csrf = state.csrf_token; button.disabled = false;
    } catch (failure) { error.textContent = failure.message; setTimeout(session, 3000); }
  }
  form.addEventListener('submit', async event => {
    event.preventDefault(); button.disabled = true; error.textContent = '';
    try {
      const response = await fetch(new URL('login', api), {
        method:'POST', credentials:'same-origin',
        headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
        body:JSON.stringify({username:form.elements.username.value,password:form.elements.password.value})
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error?.message || 'Не удалось войти.');
      form.elements.password.value = '';
      location.replace(new URL('./', document.baseURI));
    } catch (failure) { error.textContent = failure.message; button.disabled = false; }
  });
  session();
})();
