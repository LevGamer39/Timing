// Демо-бот MAX и кнопки «Демо-доступ». Только для показа без сервера: в настоящем продукте этим занимается бот в MAX.
// Чтобы убрать, удалите файлы demo-bot.*, строку импорта в js/main.js, ссылку на demo-bot.css в index.html и "demo-bot" в html/manifest.json.
import { fragment } from './templates.js';
import * as store from './store.js';
import { esc } from './ui.js';
import { copyCode } from './clipboard.js';
import { loginAddons } from './login.js';

const DEMO = [['belov.artem', 'Ученик · Артём'], ['belova.anna', 'Родитель · Анна'], ['ivanova.maria', 'Учитель · Мария Петровна'], ['kuznetsova.elena', 'Администратор · Елена Андреевна']];

let botLogin = '', botCode = '', botMessage = '', inviteValue = '', needsLogin = false, suggestions = [], botError = false;
let maxUserId = 'demo-max-' + crypto.randomUUID();
let chosenLogin = '', chosenError = '';
store.onContextChange(() => { botLogin = ''; botCode = ''; botMessage = ''; inviteValue = ''; needsLogin = false; suggestions = []; botError = false; chosenLogin = ''; chosenError = ''; });

function botHtml() {
  const profile = store.botAccount(maxUserId);
  const loginForm = needsLogin ? fragment('demo-bot', 'choose-login', [esc(chosenLogin), String(!!chosenError), chosenError ? '' : ' hidden', esc(chosenError),
    suggestions.map((name) => '<button type="button" class="btn ghost sm" data-suggest="' + esc(name) + '">' + esc(name) + '</button>').join('')]) : '';
  const passwordForm = profile ? fragment('demo-bot', 'password') : '';
  const who = profile ? fragment('demo-bot', 'profile', [esc(profile.login)]) : '';
  return fragment('demo-bot', 'bot', [esc(botLogin), chosenError ? '' : esc(botMessage), botCode ? copyCode(botCode, 'код входа') : '', esc(inviteValue), loginForm, who, passwordForm, botError ? 'class="error" role="alert"' : 'class="hint"']);
}

loginAddons.push({
  // Симуляция бота — только в режиме «Войти через MAX».
  html: (mode) => (mode === 'max' ? botHtml() : ''),
  footer: () => fragment('demo-bot', 'demo-access', [DEMO.map(([key, label]) => '<button class="btn ghost sm grow" type="button" data-demo="' + key + '">' + esc(label) + '</button>').join('')]),
  mount(root, { rerender, enter, setLogin }) {
    const say = (result, ok) => { botError = !!result.error; botMessage = result.error || ok; };
    root.querySelectorAll('[data-demo]').forEach((b) => b.addEventListener('click', () => enter(b.dataset.demo)));
    root.querySelector('#invite-f')?.addEventListener('submit', (e) => {
      e.preventDefault(); inviteValue = root.querySelector('#invite').value;
      const result = store.redeemInvitation(inviteValue, { maxUserId });
      botCode = ''; needsLogin = !!result.needsLogin; suggestions = []; chosenLogin = ''; chosenError = '';
      say(result, needsLogin ? 'Код принят. Теперь придумайте логин.' : 'Ребёнок привязан к вашему аккаунту. Логин: ' + result.login + '.');
      if (result.login) { botLogin = result.login; inviteValue = ''; setLogin(result.login); }
      rerender();
      if (needsLogin) document.getElementById('choose-login')?.focus();
    });
    root.querySelector('#choose-login-f')?.addEventListener('submit', (e) => {
      e.preventDefault(); chosenLogin = root.querySelector('#choose-login').value;
      const result = store.redeemInvitation(inviteValue, { login: chosenLogin, maxUserId });
      chosenError = result.error || ''; suggestions = result.suggestions || [];
      say(result, 'Регистрация завершена. Ваш логин: ' + result.login + '. Задайте пароль в боте или нажмите «Вход».');
      if (result.login) { botLogin = result.login; needsLogin = false; inviteValue = ''; setLogin(result.login); }
      rerender();
      if (chosenError) {
        document.getElementById('choose-login')?.focus({ preventScroll: true });
        document.getElementById('choose-login-f')?.scrollIntoView?.({ block: 'center' });
      }
    });
    root.querySelectorAll('[data-suggest]').forEach((button) => button.addEventListener('click', () => { root.querySelector('#choose-login').value = button.dataset.suggest; root.querySelector('#choose-login').focus(); }));
    root.querySelector('#new-max-person')?.addEventListener('click', () => { maxUserId = 'demo-max-' + crypto.randomUUID(); botLogin = ''; botCode = ''; botMessage = ''; needsLogin = false; inviteValue = ''; suggestions = []; rerender(); });
    root.querySelector('#bot-secret-f')?.addEventListener('submit', async (e) => {
      e.preventDefault(); const button = e.target.querySelector('button'); button.disabled = true;
      try { say(await store.botSetPassword(maxUserId, root.querySelector('#bot-secret').value), 'Пароль сохранён в боте. Теперь можно войти по логину и паролю.'); rerender(); }
      finally { button.disabled = false; }
    });
    root.querySelector('#bot-f')?.addEventListener('submit', (e) => {
      e.preventDefault(); botLogin = root.querySelector('#bot-login').value.trim().toLowerCase();
      const selected = store.selectDemoMaxProfile(botLogin, maxUserId);
      const result = selected.error ? selected : store.requestLoginCode(botLogin);
      say(result, 'Код готов. Введите его выше вместе с логином.'); botCode = result.code || '';
      if (!result.error) setLogin(botLogin);
      rerender();
    });
  }
});
