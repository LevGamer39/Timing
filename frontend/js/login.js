import { fragment } from './templates.js';
import * as store from './store.js';
import * as api from './api.js';
import { esc, logo, toast, sheet } from './ui.js';

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
        : '<p class="hint">Логин и пароль вы задаёте при регистрации в боте «Вектор» в MAX. Или войдите по коду из бота.</p>',
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

      const verifySheet = (email) => sheet(fragment('login', 'verify', [esc(email)]), (el, close) => {
        const err = el.querySelector('#vf-err');
        const fail = (text) => { err.textContent = text; err.hidden = false; };
        el.querySelector('#verify-f').addEventListener('submit', (e) => {
          e.preventDefault();
          const code = el.querySelector('#vf-code').value.trim();
          if (!/^\d{6}$/.test(code)) return fail('Код — 6 цифр.');
          store.verifyAccount(code).then((message) => {
            if (message) return fail(message);
            close();
            loginValue = email; mode = 'password'; rerender();
            toast('Аккаунт подтверждён. Войдите с паролем.');
          });
        });
        el.querySelector('#vf-again').addEventListener('click', () => store.resendCode(email).then((message) => (message ? fail(message) : toast('Письмо отправлено ещё раз'))));
      });

      root.querySelector('#register-open')?.addEventListener('click', () => sheet(fragment('login', 'reg', []), (el, close) => {
        const role = el.querySelector('#rg-role'), wrap = el.querySelector('#rg-invite-wrap'), err = el.querySelector('#rg-err');
        const fail = (text) => { err.textContent = text; err.hidden = false; };
        role.addEventListener('change', () => { wrap.hidden = role.value !== 'teacher'; });
        el.querySelector('#reg-f').addEventListener('submit', (e) => {
          e.preventDefault();
          const body = {
            first_name: el.querySelector('#rg-first').value.trim(),
            last_name: el.querySelector('#rg-last').value.trim(),
            email: el.querySelector('#rg-email').value.trim(),
            password: el.querySelector('#rg-pass').value,
            role: role.value
          };
          if (!body.first_name || !body.last_name) return fail('Введите имя и фамилию.');
          if (!body.email) return fail('Введите email.');
          if (body.password.length < 8) return fail('Пароль должен быть не короче 8 символов.');
          if (role.value === 'teacher') {
            body.invite_code = el.querySelector('#rg-invite').value.trim().toUpperCase();
            if (!body.invite_code) return fail('Для учителя нужен код от администратора.');
          }
          store.registerAccount(body).then((message) => {
            if (message) return fail(message);
            close(); verifySheet(body.email);
          });
        });
      }));

      root.querySelector('#reset-open')?.addEventListener('click', () => sheet(fragment('login', 'reset', []), (el, close) => {
        const err = el.querySelector('#rs-err');
        el.querySelector('#reset-f').addEventListener('submit', (e) => {
          e.preventDefault();
          store.requestReset(el.querySelector('#rs-email').value.trim()).then((message) => {
            if (message) { err.textContent = message; err.hidden = false; return; }
            close(); toast('Если адрес зарегистрирован, письмо со ссылкой отправлено.');
          });
        });
      }));
      root.querySelectorAll('[data-oauth]').forEach((button) => button.addEventListener('click', async () => {
        if (!/яндекс/i.test(button.dataset.oauth || '')) return toast('Вход через ' + button.dataset.oauth + ' выполняется в боте MAX');
        try { const { url } = await api.yandexLoginUrl(); location.assign(url); }
        catch (error) { toast(error.message); }
      }));
      root.querySelector('#login-f').addEventListener('submit', async (e) => {
        e.preventDefault(); const key = root.querySelector('#login').value, secret = root.querySelector('#secret').value, button = e.target.querySelector('[type=submit]');
        button.disabled = true;
        try {
          const result = await (mode === 'max' ? store.verifyLoginCode(key, secret) : store.passwordLogin(key, secret));
          if (result.error) { const error = root.querySelector('#login-error'); error.textContent = result.error; error.hidden = false; }
          else enter(result.login);
        } catch { const error = root.querySelector('#login-error'); error.textContent = 'Не удалось войти. Попробуйте ещё раз.'; error.hidden = false; }
        finally { button.disabled = false; }
      });
      loginAddons.forEach((addon) => addon.mount?.(root, { rerender, enter, setLogin: (value) => { loginValue = value; } }));
    }
  };
}
function resetPasswordView({ params, go }) {
  const token = params[0] || '';
  return {
    html: fragment('login', 'newpass', [logo(56)]),
    mount(root) {
      const err = root.querySelector('#np-err');
      const fail = (text) => { err.textContent = text; err.hidden = false; };
      root.querySelector('#newpass-f').addEventListener('submit', (e) => {
        e.preventDefault();
        const pass = root.querySelector('#np-pass').value, rep = root.querySelector('#np-rep').value;
        if (pass.length < 8) return fail('Пароль должен быть не короче 8 символов.');
        if (pass !== rep) return fail('Пароль и повтор не совпадают.');
        store.confirmReset(token, pass).then((message) => {
          if (message) return fail(message);
          toast('Пароль обновлён. Войдите с новым паролем.');
          go('login');
        });
      });
    }
  };
}

function confirmEmailView({ params }) {
  return {
    html: fragment('login', 'mailconfirm', [logo(56)]),
    mount(root) {
      const text = root.querySelector('#mc-text');
      store.confirmEmail(params[0] || '').then((message) => {
        text.textContent = message || 'Email обновлён. Теперь входите с новым адресом.';
      });
    }
  };
}

export { loginView, resetPasswordView, confirmEmailView };
