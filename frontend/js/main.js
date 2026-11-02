import * as store from './store.js';
import { loadTemplates } from './templates.js';
import { applyAccent } from './theme.js';
import { bindCopy } from './clipboard.js';
import { icon, logo, esc, setBounds, avatarSvg, closeSheets } from './ui.js';
import { notificationButton, openNotifications } from './common.js';
import { loginView, resetPasswordView, confirmEmailView } from './login.js';
import './demo-bot.js'; // демо-бот MAX и «Демо-доступ»: без сервера; удалить вместе с demo-bot.*
import { homeView } from './home.js';
import { diaryView } from './diary.js';
import { gradesView } from './grades.js';
import { subjectView } from './subject.js';
import { mailView } from './mail.js';
import { composeView } from './compose.js';
import { aiView } from './ai.js';
import { settingsView } from './settings.js';
import { tasksView } from './tasks.js';
import { teacherHomeView } from './teacher-home.js';
import { journalView } from './journal.js';
import { analysisView } from './analysis.js';
import { myClassView } from './my-class.js';
import { adminHomeView } from './admin-home.js';
import { classesView } from './classes.js';
import { teachersView } from './teachers.js';
import { scheduleView } from './schedule.js';
import { attendanceView } from './attendance.js';

const app = document.getElementById('app');
// Письма из бэкенда ведут на /reset-password-new и /settings/change-email/confirm
// с токеном в query; SPA живёт на хеше, поэтому переводим адрес сразу.
(() => {
  const legacy = location.pathname.match(/^\/(reset-password-new|settings\/change-email\/confirm)$/);
  if (!legacy) return;
  const token = new URL(location.href).searchParams.get('token') || '';
  const hash = legacy[1].startsWith('reset') ? '#/reset/' + token : '#/confirm-email/' + token;
  history.replaceState({}, '', '/#/' + hash.slice(2));
})();
let mountedScreen, screenData, screenRights;
bindCopy(document);
document.addEventListener('click', (event) => {
  if (event.target.closest('#notif')) openNotifications();
});
const LEARNER = ['student', 'parent'];
const TEACHER = ['teacher'];
const ADMIN = ['admin'];
const STAFF = ['admin', 'teacher'];

// [путь, экран, раздел меню, экран входа, роли, показывать нижнее меню на мобильном экране]
const ROUTES = [
  [/^login$/, loginView, null, true],
  [/^reset\/([\w-]+)$/, resetPasswordView, null, true],
  [/^confirm-email\/([\w-]+)$/, confirmEmailView, null, true],
  [/^home$/, homeView, 'home', false, LEARNER, true],
  [/^home$/, teacherHomeView, 'home', false, TEACHER, true],
  [/^home$/, adminHomeView, 'home', false, ADMIN, true],
  [/^diary(?:\/(-?\d+))?$/, diaryView, 'diary', false, LEARNER, true],
  [/^grades$/, gradesView, 'grades', false, LEARNER, true],
  [/^grades\/(\w+)$/, subjectView, 'grades', false, LEARNER, true],
  [/^tasks$/, tasksView, 'tasks', false, LEARNER, true],
  [/^ai$/, aiView, null, false, LEARNER, false],
  [/^journal(?:\/([\w-]+)(?:\/([\d-]+))?)?$/, journalView, 'journal', false, TEACHER, true],
  [/^journal\/analysis\/([\w-]+)\/([\d-]+)$/, analysisView, 'journal', false, TEACHER, false],
  [/^myclass$/, myClassView, 'myclass', false, TEACHER, true],
  [/^classes(?:\/([\w-]+))?$/, classesView, 'classes', false, STAFF, true],
  [/^teachers$/, teachersView, 'teachers', false, ADMIN, true],
  [/^schedule(?:\/([\w-]+))?$/, scheduleView, 'schedule', false, ADMIN, true],
  [/^attendance$/, attendanceView, 'attendance', false, ADMIN, true],
  [/^mail(?:\/(sent))?$/, mailView, 'mail', false, null, true],
  [/^mail\/read\/(\w+)$/, mailView, 'mail', false, null, true],
  [/^mail\/new(?:\/(\w+))?$/, composeView, 'mail', false, null, false],
  [/^settings$/, settingsView, 'settings', false, null, false],
  [/^profile$/, null]
];

const MENU = {
  student: [['home', 'Главная', 'home'], ['diary', 'Дневник', 'diary'], ['grades', 'Оценки', 'grades'], ['tasks', 'Задачи', 'check'], ['mail', 'Почта', 'mail']],
  teacher: [['home', 'Главная', 'home'], ['journal', 'Журнал', 'table'], ['myclass', 'Мой класс', 'users'], ['mail', 'Почта', 'mail']],
  admin: [['home', 'Главная', 'home'], ['classes', 'Классы', 'users'], ['teachers', 'Учителя', 'teacher'], ['schedule', 'Расписание', 'calendar', 'Уроки'], ['attendance', 'Пропуски', 'absent'], ['mail', 'Почта', 'mail']]
};
MENU.parent = MENU.student;

export const go = (path) => { location.hash = '#/' + path; };

function applyTheme() {
  document.documentElement.dataset.theme = store.resolvedTheme();
  applyAccent(store.get().accent, store.resolvedTheme());
}
applyTheme();

const menuItems = (active, desk) => {
  const unread = store.unreadCount();
  return MENU[store.role()].map(([key, label, ic, short]) => {
    const badge = key === 'mail' && unread ? '<span class="badge" aria-label="' + unread + ' непрочитанных">' + unread + '</span>' : '';
    // Короткая подпись для нижнего меню: 6 пунктов не помещаются.
    return '<a href="#/' + key + '"' + (active === key ? ' class="on" aria-current="page"' : '') + (!desk && short ? ' aria-label="' + label + '"' : '') + '>' + (desk ? '' : icon(ic)) + (!desk && short ? short : label) + badge + '</a>';
  }).join('');
};

const bottomNav = (active) => '<nav class="nav' + (MENU[store.role()].length > 5 ? ' many' : '') + '" aria-label="Разделы">' + menuItems(active, false) + '</nav>';

function topbar(active) {
  const acc = store.account();
  const learner = LEARNER.includes(store.role());
  return `
<header class="topbar">
  <a class="brand" href="#/home" aria-label="Вектор, на главную">${logo(30)}<span>Вектор</span></a>
  <nav class="tabs" aria-label="Разделы"><div class="tabs-in">${menuItems(active, true)}</div></nav>
  <div class="top-act">
    ${learner ? `<a class="btn ai-secondary sm" href="#/ai">${icon('spark', 18)}Спросить ИИ</a>` : ''}
    ${notificationButton()}
    <a class="icon-btn gear" href="#/settings" aria-label="Настройки"${active === 'settings' ? ' aria-current="page"' : ''}>${icon('gear', 20)}</a>
    <a class="avatar" href="#/settings" aria-label="${store.avatarOf() ? '' : esc(acc.initials) + ' — '}Профиль и настройки"${store.avatarOf() ? ' style="background: none"' : ''}>${store.avatarOf() ? avatarSvg(store.avatarOf(), 44) : esc(acc.initials)}</a>
  </div>
</header>`;
}

function rights() {
  const key = location.hash.match(/^#\/journal\/([\w-]+)(?:\/[\d-]+)?$/)?.[1];
  return JSON.stringify([store.get().session, store.account(), store.role() === 'teacher' ? (key ? store.journalKeys().some((j) => j.key === key) : store.journalKeys()) : null]);
}

function dataForScreen() {
  const path = location.hash.replace(/^#\/?/, ''), s = store.get();
  if (/^journal(?:\/|$)/.test(path) && store.role() === 'teacher') {
    const key = path.split('/')[1] || store.journalKeys()[0]?.key;
    return JSON.stringify([key && store.journalKeys().some((j) => j.key === key) ? store.journal(key, true) : null, store.workTypes(), store.school(), s.schedules, s.schedTeachers]);
  }
  const fields = /^(mail|compose)/.test(path) ? ['readMail', 'deletedMail', 'sent', 'teachers', 'classes', 'accounts']
    : /^(grades)/.test(path) ? ['journals', 'classes', 'teachers', 'schedules', 'schedTeachers', 'school']
    : /^(diary)/.test(path) ? ['journals', 'classes', 'schedules', 'school', 'attendance', 'attendanceSubjects', 'hwDone']
    : /^(teachers)/.test(path) ? ['teachers', 'classes', 'invitations', 'maxDone']
    : /^(schedule)/.test(path) ? ['teachers', 'classes', 'schedules', 'schedTeachers', 'school']
    : /^(classes)/.test(path) ? ['classes', 'teachers', 'accounts', 'invitations', 'maxDone']
    : /^(settings)/.test(path) ? ['school', 'theme', 'accent', 'avatars', 'classes']
    : Object.keys(s).filter((k) => !['revision', 'session', 'theme', 'accent'].includes(k));
  return JSON.stringify(fields.map((k) => s[k]));
}

function render(external = false) {
  applyTheme();
  const sc = store.school();
  setBounds(store.boundsFor(sc, sc.scale).map((v) => v * 100 / sc.scale));
  let path = location.hash.replace(/^#\/?/, '') || 'home';
  if (path === 'profile') return go('settings');
  const s = store.get();
  const acc = store.account();

  if (!s.session || !acc) { if (!/^(login|reset\/|confirm-email\/)/.test(path)) { go('login'); path = 'login'; } }
  else if (path === 'login') return go('home');

  const role = store.role();
  const hit = ROUTES
    .filter(([, view, , , roles]) => view && (!roles || roles.includes(role)))
    .map(([re, view, navKey, auth, , mobileNav]) => ({ m: path.match(re), view, navKey, auth, mobileNav }))
    .find((r) => r.m);
  if (!hit) return go('home');

  const pageNames = { loginView: 'login', resetPasswordView: 'reset', confirmEmailView: 'confirm-email', homeView: 'home', teacherHomeView: 'teacher-home', adminHomeView: 'admin-home', diaryView: 'diary', gradesView: 'grades', subjectView: 'subject', tasksView: 'tasks', mailView: 'mail', composeView: 'compose', aiView: 'ai', settingsView: 'settings', journalView: 'journal', analysisView: 'analysis', myClassView: 'my-class', classesView: 'classes', teachersView: 'teachers', scheduleView: 'schedule', attendanceView: 'attendance' };
  document.body.dataset.page = pageNames[hit.view.name];
  document.body.classList.toggle("login-visual", path === "login");
  const create = () => hit.view({ params: hit.m.slice(1), path, go, rerender: render });
  const screen = external === true ? store.withoutWrites(create) : create();
  if (hit.auth) app.innerHTML = screen.html;
  else app.innerHTML = topbar(hit.navKey) + '<main class="screen' + (hit.mobileNav ? ' with-nav' : '') + '">' + screen.html + '</main>' + (hit.mobileNav ? bottomNav(hit.navKey) : '');
  if (screen.mount) {
    const mount = () => screen.mount(app, { external: external === true });
    if (external === true) store.withoutWrites(mount); else mount();
  }
  mountedScreen = screen;
  screenData = dataForScreen(); screenRights = rights();
  const h1 = app.querySelector('main h1, .auth h1');
  document.title = (h1 ? h1.textContent.trim() + ' · ' : '') + 'Вектор';
}

let lastPath = '';
try {
  await loadTemplates();
  await store.bootstrap();
  let queued = false;
  store.onDataChange((_conflict, _changed, external = true) => {
    if (!external) {
      if (!queued) { screenData = dataForScreen(); screenRights = rights(); }
      return;
    }
    if (queued) return;
    queued = true;
    queueMicrotask(() => {
      queued = false;
      applyTheme();
      const changedRights = screenRights !== rights();
      if (!changedRights && !store.isServer() && screenData === dataForScreen()) return;
      // Редактор журнала восстанавливает введённое поверх новых данных, пока права действуют.
      const draft = mountedScreen?.captureDraft?.();
      closeSheets();
      render(true);
      if (!changedRights && draft) mountedScreen?.restoreDraft?.(draft);
    });
  });
  window.addEventListener('hashchange', () => {
    const p = location.hash;
    render();
    if (p !== lastPath && !app.querySelector('[data-keep-scroll]')) window.scrollTo(0, 0);
    lastPath = p;
  });
  if (window.matchMedia) window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', render);
  lastPath = location.hash;
  render();
}
catch (error) { console.error(error); app.innerHTML = '<main class="auth card pad"><h1>Не удалось открыть Вектор</h1><p>Проверьте подключение и обновите страницу.</p><button class="btn" id="retry" type="button">Повторить</button></main>'; document.getElementById('retry').addEventListener('click', () => location.reload()); }
