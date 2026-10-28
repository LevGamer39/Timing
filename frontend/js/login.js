import { fragment } from './templates.js';
import * as store from './store.js';
import { esc, logo, toast } from './ui.js';

// Дополнения экрана входа. Сейчас их подключает только демо-бот (demo-bot.js); без него вход работает сам по себе.
export const loginAddons = [];

let mode = typeof location !== 'undefined' && new URL(location.href).searchParams.has('demo-bot') ? 'max' : 'password', loginValue = '';
store.onContextChange(() => { mode = 'password'; loginValue = ''; });

function loginView({ go, rerender }) {
  const enter = (key) => { if (!store.login(key)) return toast(store.SAVE_ERROR); go('home'); };
  const max = mode === 'max';
  return {
    html: fragment('login', 'screen', [logo(56), esc(loginValue), max ? 'Код из MAX' : 'Пароль', max ? 'text' : 'password',
      max ? 'inputmode="numeric" maxlength="6" autocomplete="one-time-code"' : 'autocomplete="current-password"',
      max ? '<button class="btn ghost block" type="button" id="password-login">Войти по паролю</button>' : '',
      max ? '<p class="hint">В боте «Вектор» в MAX нажмите «Вход», затем введите здесь свой логин и полученный код. Код действует 5 минут.</p>'
        : '<p class="hint">Логин вы придумываете в боте по приглашению школы. Пароль задаётся в боте «Вектор» в MAX.</p>',
      loginAddons.map((addon) => addon.html?.(mode) ?? '').join(''),
      loginAddons.map((addon) => addon.footer?.() ?? '').join(''),
      max && loginAddons.length ? 'auth-max' : '']),
    mount(root) {
      const remember = () => { loginValue = root.querySelector('#login').value.trim(); };
      root.querySelector('#max-login').addEventListener('click', () => {
        if (mode === 'max') { root.querySelector('#secret').focus(); return; }
        remember(); mode = 'max'; rerender(); document.getElementById(loginValue ? 'secret' : 'login')?.focus();
      });
      root.querySelector('#password-login')?.addEventListener('click', () => { remember(); mode = 'password'; rerender(); });
      root.querySelectorAll('[data-oauth]').forEach((button) => button.addEventListener('click', () => toast('Вход с ' + button.dataset.oauth + ' пока не подключён')));
      root.querySelector('#login-f').addEventListener('submit', async (e) => {
        e.preventDefault(); const key = root.querySelector('#login').value, secret = root.querySelector('#secret').value, button = e.target.querySelector('[type=submit]');
        button.disabled = true;
        try {
          const result = mode === 'max' ? store.verifyLoginCode(key, secret) : await store.passwordLogin(key, secret);
          if (result.error) { const error = root.querySelector('#login-error'); error.textContent = result.error; error.hidden = false; }
          else enter(result.login);
        } catch { const error = root.querySelector('#login-error'); error.textContent = 'Не удалось войти. Попробуйте ещё раз.'; error.hidden = false; }
        finally { button.disabled = false; }
      });
      loginAddons.forEach((addon) => addon.mount?.(root, { rerender, enter, setLogin: (value) => { loginValue = value; } }));
    }
  };
}
export { loginView };
