import { NOW, SCHOOL, TIMES as DEFAULT_TIMES, SUBJECTS, STUDENTS, ACCOUNTS, HOMEWORK, HW_DONE, GRADES, ABSENT, PEOPLE, INBOX, SENT } from './data.js';
import { WORK_TYPES, JOURNALS } from './teacher-data.js';
import { CLASSES, TEACHERS, SCHEDULES, SCHOOL_SETTINGS, loginOf } from './admin-data.js';
import { normalizedScore, configureGrading } from './grading.js';
import { iso, parse, addDays, mondayOf, weekday, weighted, mark } from './ui.js';
import * as api from './api.js';
import * as server from './server.js';

const KEY = 'timing-demo-v1';
export const SAVE_ERROR = 'Не удалось сохранить. Данные могли измениться в другой вкладке. Повторите действие.';
const DEFAULTS = {
  session: null,                 // { login, role, childId }
  maxDone: {},                   // логин → уже входил через MAX
  accent: 'orange',
  theme: 'system',               // light | dark | system
  hwDone: Object.fromEntries(HW_DONE.map((k) => [k, true])),
  seen: {},                      // ученик → оценки, которые уже видели
  readMail: {},                  // id письма → прочитано
  sent: {}                       // роль → свои отправленные
};

let stored = readStorage();
let state = load(stored);
let generation = 0;
const journalSnapshots = new WeakMap();
const contextListeners = new Set();
const dataListeners = new Set();
let readOnly = false;
export function withoutWrites(render) {
  const previous = readOnly; readOnly = true;
  try { return render(); } finally { readOnly = previous; }
}
export const onContextChange = (resetView) => contextListeners.add(resetView);
export const onDataChange = (render) => dataListeners.add(render);
const resetViewContext = () => contextListeners.forEach((resetView) => resetView());
function readStorage() {
  try { return localStorage.getItem(KEY); } catch { return null; }
}
function load(raw) {
  try { return Object.assign({}, DEFAULTS, JSON.parse(raw || '{}')); }
  catch { return Object.assign({}, DEFAULTS); }
}
function refresh(conflict = false) {
  const latest = readStorage();
  if (latest === stored) return false;
  const previous = state, session = state.session;
  stored = latest;
  state = load(latest);
  generation++;
  // Серверная сессия живёт в памяти и не проверяется по демо-данным: иначе
  // запись timing-demo-v1 из другой вкладки выкидывала вошедшего на форму входа.
  state.session = session && (serverSession || accountByLogin(session.login)) ? session : null;
  if (state.session?.role === 'parent') {
    const ids = account().children;
    if (!ids.includes(state.session.childId)) state.session = { ...state.session, childId: ids[0] };
  }
  if (pending && !accountByLogin(pending.login)) pending = null;
  if (JSON.stringify(previous.session) !== JSON.stringify(state.session)) resetViewContext();
  const s = school();
  configureGrading(s.scale, boundsFor(s, s.scale));
  const changed = Object.keys({ ...previous, ...state }).filter((k) => k !== 'revision' && JSON.stringify(previous[k]) !== JSON.stringify(state[k]));
  dataListeners.forEach((render) => render(conflict, changed));
  return true;
}
export function save() {
  if (readOnly) return false;
  // В серверном режиме данные живут в памяти: localStorage — только демо.
  if (serverSession) {
    dataListeners.forEach((render) => render(false, [], false));
    return true;
  }
  // Сравнение снимка защищает и от старых версий демо, ещё не записывающих revision.
  if (refresh(true)) return false;
  try {
    const unchanged = (value) => JSON.stringify({ ...value, revision: undefined, session: undefined });
    const previous = load(stored);
    if (unchanged(previous) === unchanged(state) && JSON.stringify(previous.session) === JSON.stringify(state.session)) return true;
    const revision = crypto.randomUUID(), raw = JSON.stringify({ ...state, revision });
    // Последняя проверка сужает окно гонки; атомарного compare-and-swap у localStorage нет.
    if (refresh(true)) return false;
    localStorage.setItem(KEY, raw);
    stored = raw;
    state.revision = revision;
  } catch {
    const session = state.session;
    state = load(stored); state.session = session;
    return false;
  }
  dataListeners.forEach((render) => render(false, [], false));
  return true;
}
export const get = () => state;
export function set(patch) {
  if (readOnly) return false;
  if (!serverSession && refresh(true)) return false;
  if (Object.entries(patch).every(([k, v]) => JSON.stringify(state[k]) === JSON.stringify(v))) return true;
  const session = state.session, before = generation;
  state = Object.assign({}, state, JSON.parse(JSON.stringify(patch)));
  const ok = save();
  if (!ok && generation === before) state.session = session;
  return ok;
}
export function reset() { resetViewContext(); stored = readStorage(); state = JSON.parse(JSON.stringify(DEFAULTS)); save(); pending = null; configureGrading(100, [40, 60, 80]); }
if (typeof window !== 'undefined') window.addEventListener?.('storage', (event) => {
  if ((event.key === KEY || event.key === null) && (!event.storageArea || event.storageArea === localStorage)) refresh();
});

export function resolvedTheme() {
  if (state.theme === 'light' || state.theme === 'dark') return state.theme;
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export const CODE_TTL_MIN = 5, CODE_TRIES = 5, CODE_RESEND_SEC = 60;
let pending = null;
const randomCode = () => {
  const values = new Uint32Array(1);
  crypto.getRandomValues(values);
  return String(100000 + values[0] % 900000);
};
const accountKey = (value) => Object.keys(state.loginNames || {}).find((key) => state.loginNames[key] === value) || value;
export const maxLinked = (value) => {
  const key = accountKey(value);
  return state.invitations?.[key]?.used ?? state.maxDone?.[key] ?? (accountByLogin(key)?.role === 'student' ? Object.values(classes()).some((c) => c.students.some((s) => s.login === key && s.status === 'ok')) : !!ACCOUNTS[key]);
};
const inviteCode = (item) => item?.invite_code || item?.code;
const invitationResult = (item) => ({ ...item, invite_code: inviteCode(item), code: inviteCode(item) });
export function invitation(key, renew = false) {
  key = accountKey(key);
  if (!accountByLogin(key)) return null;
  if (maxLinked(key)) return { used: true };
  if (!renew && inviteCode(state.invitations?.[key])) return invitationResult(state.invitations[key]);
  const taken = new Set(Object.values(state.invitations || {}).map(inviteCode));
  let code;
  do { code = 'VK-' + randomCode() + '-' + randomCode(); } while (taken.has(code));
  const item = { invite_code: code, used: false, schoolId: SCHOOL_ID };
  return set({ invitations: { ...state.invitations, [key]: item } }) ? invitationResult(item) : null;
}
export const SCHOOL_ID = 'school-17';
export const loginName = (key) => state.loginNames?.[key] || key;
const credentialKey = (value) => {
  const name = String(value).trim().toLowerCase();
  return Object.keys(state.loginNames || {}).find((key) => loginName(key) === name) || (loginName(name) === name ? name : '');
};
const loginTaken = (name, own) => name !== own && (!!accountByLogin(name) || !!state.accounts?.[name] || !!ACCOUNTS[name] || state.retiredLogins?.includes(name)) || Object.entries(state.loginNames || {}).some(([key, value]) => key !== own && value === name);
export function checkLogin(value, own) {
  const name = String(value || '').trim().toLowerCase();
  if (!/^[a-z0-9.]{4,32}$/.test(name)) return { error: 'Логин: 4–32 символа, только латиница, цифры и точка.', errorCode: 'LOGIN_INVALID' };
  if (loginTaken(name, own)) {
    const suggestions = [];
    for (let i = 2; suggestions.length < 3; i++) { const candidate = name.slice(0, 32 - String(i).length) + i; if (!loginTaken(candidate, own)) suggestions.push(candidate); }
    return { error: 'Этот логин занят. Выберите другой или один из вариантов.', errorCode: 'LOGIN_TAKEN', suggestions };
  }
  return { login: name };
}
export function inspectInvitation(value) {
  refresh();
  const code = String(value).trim().toUpperCase();
  const match = Object.entries(state.invitations || {}).find(([, i]) => inviteCode(i) === code);
  if (match?.[1].schoolId && match[1].schoolId !== SCHOOL_ID) return { error: 'Это код другой школы. Попросите приглашение в своей школе.', errorCode: 'INVITE_WRONG_SCHOOL' };
  if (match?.[1].used) return { error: 'Этот код уже использован. Для входа нажмите «Вход» в боте; для другого человека получите новый код в школе.', errorCode: 'INVITE_USED' };
  if (!match || !accountByLogin(match[0])) return { error: 'Код не найден. Проверьте его или попросите новый в школе.', errorCode: 'INVITE_INVALID' };
  return { key: match[0], item: match[1], account: accountByLogin(match[0]) };
}
// MAX-профиль задаётся симулятором; в production он приходит из проверенного webhook.
export function redeemInvitation(value, { login: chosen, maxUserId = 'demo-max', schoolId = SCHOOL_ID } = {}) {
  const inspected = inspectInvitation(value);
  if (inspected.error) return inspected;
  if (schoolId !== SCHOOL_ID) return { error: 'Это код другой школы. Попросите приглашение в своей школе.', errorCode: 'INVITE_WRONG_SCHOOL' };
  const { key, item, account: invited } = inspected;
  const linkedKey = state.maxProfiles?.[maxUserId];
  const existing = linkedKey && maxLinked(linkedKey) && accountByLogin(linkedKey);
  if (existing && (existing.role !== 'parent' || invited.role !== 'parent')) return { error: 'Этот MAX-профиль уже связан с аккаунтом. Второй код можно добавить только родителю для другого ребёнка.', errorCode: 'MAX_ALREADY_LINKED' };
  if (!existing && chosen === undefined) return { needsLogin: true, name: invited.name };
  const checked = existing ? { login: loginName(linkedKey) } : checkLogin(chosen, key);
  if (checked.error) return checked;
  const target = existing ? linkedKey : key;
  const accounts = { ...state.accounts }, all = classes();
  if (existing) {
    accounts[target] = { ...existing, children: [...new Set([...existing.children, ...invited.children])] };
    delete accounts[key];
  }
  Object.values(all).forEach((c) => c.students.forEach((s) => {
    if (s.login === key) s.status = 'ok';
    if (invited.role === 'parent' && invited.children.includes(studentIdForLogin(s.login))) {
      s.parentLogins = [...new Set(parentsForStudent(s).map((login) => login === key ? target : login))];
      if (s.parentLogin === key) { s.parentLogin = target; s.parent = (existing || invited).name; }
    }
  }));
  const patch = { accounts, classes: all, invitations: { ...state.invitations, [key]: { ...item, used: true, maxUserId, accountKey: target } }, maxDone: { ...state.maxDone, [target]: true }, maxProfiles: { ...state.maxProfiles, [maxUserId]: target }, loginNames: { ...state.loginNames, [target]: checked.login } };
  if (existing && key !== target) patch.retiredLogins = [...new Set([...(state.retiredLogins || []), key])];
  if (!set(patch)) return { error: SAVE_ERROR };
  return { login: checked.login, accountKey: target, account: accountByLogin(target), linkedChild: !!existing };
}
export const botAccount = (maxUserId) => {
  const key = state.maxProfiles?.[maxUserId];
  return key && maxLinked(key) && accountByLogin(key) ? { key, login: loginName(key), account: accountByLogin(key) } : null;
};
export function selectDemoMaxProfile(value, maxUserId) {
  refresh();
  const key = credentialKey(value);
  if (!accountByLogin(key) || !maxLinked(key)) return { error: 'Профиль не найден. Сначала отправьте приглашение школы.' };
  if (!set({ maxProfiles: { ...state.maxProfiles, [maxUserId]: key } })) return { error: SAVE_ERROR };
  return { login: loginName(key) };
}
export async function botSetPassword(maxUserId, password) {
  refresh();
  const profile = botAccount(maxUserId);
  if (!profile) return { error: 'Сначала зарегистрируйтесь в боте.' };
  if (String(password).length < 8) return { error: 'Пароль должен содержать не меньше 8 символов.' };
  const salt = Array.from(crypto.getRandomValues(new Uint8Array(16)));
  const hash = await passwordHash(password, salt);
  refresh();
  if (botAccount(maxUserId)?.key !== profile.key) return { error: 'Профиль изменился. Повторите действие.' };
  return set({ passwords: { ...state.passwords, [profile.key]: { salt, hash } } }) ? {} : { error: SAVE_ERROR };
}
export function classInvitations(classId) {
  const c = classes()[classId];
  if (!c || !(role() === 'admin' || role() === 'teacher' && homeroom()?.key === classId)) return [];
  const cards = [];
  for (const pupil of c.students) {
    for (const key of [pupil.login, ...parentsForStudent(pupil)].filter(Boolean)) {
      if (maxLinked(key) || cards.some((card) => card.key === key)) continue;
      const invite = invitation(key);
      if (!invite) return null;
      cards.push({ key, name: accountByLogin(key).name, className: c.name, role: accountByLogin(key).role, code: invite.invite_code });
    }
  }
  return cards;
}
export function requestLoginCode(value, now = Date.now()) {
  const login = credentialKey(value);
  if (!accountByLogin(login) || !maxLinked(login)) return { error: 'Сначала зарегистрируйтесь в боте по приглашению школы.' };
  if (pending?.login === login && now - pending.at < CODE_RESEND_SEC * 1000) return { login: loginName(login), code: pending.code, wait: true };
  pending = { login, code: randomCode(), at: now, tries: 0 };
  return { login: loginName(login), code: pending.code };
}
export async function verifyLoginCode(login, value, now = Date.now()) {
  // Код демо-бота проверяется локально: он создан в этой же вкладке.
  if (pending) {
    if (now - pending.at >= CODE_TTL_MIN * 60000 || !accountByLogin(pending.login) || !maxLinked(pending.login)) { pending = null; return { error: 'Код устарел. Запросите новый.', expired: true }; }
    if (credentialKey(login) !== pending.login || String(value).trim() !== pending.code) {
      if (++pending.tries >= CODE_TRIES) { pending = null; return { error: 'Слишком много попыток. Запросите новый код.', expired: true }; }
      return { error: 'Неверный логин или код. Осталось попыток: ' + (CODE_TRIES - pending.tries) + '.' };
    }
    const key = pending.login;
    pending = null;
    return { login: loginName(key) };
  }
  // Настоящий код из MAX-бота: обмен на JWT.
  try {
    const result = await api.loginMaxCode(String(value).trim());
    const { profile } = await server.load();
    adopt(profile.login ? profile : { ...profile, login: result.login || login });
    applySchoolScale();
    return { login: result.login || login };
  } catch (error) {
    return { error: error.status === 0 ? 'Сервер недоступен. Попробуйте позже.' : error.message };
  }
}
async function passwordHash(password, salt) {
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveBits']);
  const bits = await crypto.subtle.deriveBits({ name: 'PBKDF2', salt: new Uint8Array(salt), iterations: 100000, hash: 'SHA-256' }, key, 256);
  return Array.from(new Uint8Array(bits), (b) => b.toString(16).padStart(2, '0')).join('');
}
// Сессия сервера живёт отдельно от демо-состояния: токен в localStorage,
// профиль в памяти. Пока токена нет, работают демо-данные.
let serverSession = null;
let serverAccount = null;

const adopt = (profile) => {
  // Старые сборки бэкенда не отдают login: сессия всё равно серверная.
  const login = profile.login || '';
  serverSession = {
    login: login,
    role: profile.role,
    childId: profile.role === 'parent'
      ? String(server.session.children[0]?.id ?? '')
      : profile.role === 'student' ? String(profile.id) : null
  };
  serverAccount = server.accountFor(profile, server.session.children);
  server.setNotifier(() => dataListeners.forEach((render) => render(false, [])));
  NOW.setTime(Date.now());
  resetViewContext();
  state.session = { login: login, role: profile.role, childId: serverSession.childId };
};

export const isServer = () => Boolean(serverSession);

const applySchoolScale = () => {
  const settings = school();
  configureGrading(settings.scale || 100, boundsFor(settings, settings.scale || 100));
};

// initData мини-приложения MAX, если страница открыта внутри MAX.
// Мост — window.WebApp (MAX Bridge, скрипт st.max.ru/js/max-web-app.js).
export function maxWebAppInitData() {
  try {
    const w = globalThis;
    const bridge = w.MaxWebApp || w.MAXWebApp || w.Telegram?.WebApp || w.WebApp;
    const data = bridge?.initData || bridge?.init_data || bridge?.initdata;
    return typeof data === 'string' && data ? data : '';
  } catch { return ''; }
}

export async function bootstrap() {
  // Открыто из MAX через Web App: меняем initData на JWT и входим сразу.
  const initData = maxWebAppInitData();
  if (initData && !api.hasToken()) {
    try {
      const result = await api.loginMaxWebApp(initData);
      const { profile } = await server.load();
      adopt(profile.login ? profile : { ...profile, login: result.login });
      applySchoolScale();
      return true;
    } catch { /* не привязан или подпись не сошлась — обычный вход */ }
  }
  if (!api.hasToken()) return false;
  try {
    const { profile } = await server.load();
    adopt(profile);
    NOW.setTime(Date.now());
    applySchoolScale();
    return true;
  } catch {
    api.setToken('');
    server.clear();
    serverSession = null;
    serverAccount = null;
    return false;
  }
}

// Перечитать данные с сервера, не разлогинивая при сбое сети: нужен после
// того, как ИИ создал или изменил задачи.
export async function refreshData() {
  if (!serverSession) return false;
  try {
    const { profile } = await server.load();
    adopt(profile);
    applySchoolScale();
    return true;
  } catch {
    return false;
  }
}

export async function passwordLogin(value, password) {
  try {
    const result = await api.loginPassword(value, password);
    const { profile } = await server.load();
    adopt(profile.login ? profile : { ...profile, login: result.login || value });
    applySchoolScale();
    return { login: result.login || value };
  } catch (error) {
    if (error.status !== 0) return { error: error.message };
  }
  refresh();
  const key = credentialKey(value), item = state.passwords?.[key];
  if (!item || !accountByLogin(key) || !maxLinked(key) || await passwordHash(password, item.salt) !== item.hash) return { error: 'Неверный логин или пароль. Можно войти через MAX.' };
  refresh();
  if (!accountByLogin(key) || state.passwords?.[key]?.hash !== item.hash || !maxLinked(key)) return { error: 'Доступ изменился. Войдите заново.' };
  return { login: loginName(key) };
}
export function login(name) {
  refresh();
  const key = credentialKey(name);
  // Логины регистронезависимы, а поле login в ответе может отсутствовать
  // (старые сборки бэкенда): сервер уже авторизовал — пускаем.
  const serverLogin = String(serverSession?.login || '').trim().toLowerCase();
  if (serverSession && (!serverLogin || serverLogin === key)) {
    resetViewContext();
    return set({ session: { login: serverSession.login || key, role: serverSession.role, childId: serverSession.childId } });
  }
  const acc = accountByLogin(key);
  if (!acc) return false;
  resetViewContext();
  return set({ session: { login: key, role: acc.role, childId: acc.role === 'parent' ? acc.children[0] : acc.studentId || null } });
}
// Регистрация, подтверждение почты и восстановление доступа — только сервер.
const settle = (promise) => promise.then(() => '').catch((error) => (error && error.message) || 'Не удалось выполнить запрос.');
export const registerAccount = (body) => settle(api.register(body));
export const verifyAccount = (code) => settle(api.verifyEmail(code));
export const resendCode = (email) => settle(api.resendVerification(email));
export const requestReset = (email) => settle(api.requestPasswordReset(email));
export const confirmReset = (token, password) => settle(api.confirmPasswordReset(token, password));
export const changeEmail = (email) => settle(api.requestEmailChange(email));
export const confirmEmail = (token) => settle(api.confirmEmailChange(token));
export async function joinClass(code) {
  if (!serverSession) return 'Код класса работает только на сервере.';
  try {
    await api.joinClass(code);
    const data = await server.load();
    adopt(data.profile);
    return '';
  } catch (error) {
    return (error && error.message) || 'Не удалось вступить в класс.';
  }
}

export function logout() {  pending = null;
  api.setToken('');
  server.clear();
  serverSession = null;
  serverAccount = null;
  server.setNotifier(null);
  refresh();
  resetViewContext();
  set({ session: null });
  state.session = null;
};
export const account = () => serverSession ? serverAccount : state.session && accountByLogin(state.session.login);
export const role = () => serverSession ? serverSession.role : state.session && state.session.role;
export const student = () => serverSession
  ? STUDENTS[serverSession.childId] || null
  : state.session && studentById(state.session.childId);
export const children = () => serverSession
  ? server.session.children
  : (account()?.children || []).map(studentById).filter(Boolean);
export const setChild = (id) => {
  if (!children().some((c) => String(c.id) === String(id))) return;
  if (serverSession && String(serverSession.childId) !== String(id)) {
    serverSession = Object.assign({}, serverSession, { childId: String(id) });
    server.activate(id);
    resetViewContext();
    return set({ session: Object.assign({}, state.session, { childId: String(id) }) });
  }
  if (state.session.childId !== id) resetViewContext();
  return set({ session: Object.assign({}, state.session, { childId: id }) });
};

const holidayOn = (d) => serverSession ? undefined : school().periods[school().periodType].find(([n, from, to]) => /каникул/i.test(n) && iso(d) >= from && iso(d) <= to);

// Привязка родителя: ученик выдаёт код, родитель вводит его у себя.
export async function parentLinkCode() {
  if (!serverSession) return { error: 'Код родителя выдаётся только на сервере.' };
  try {
    const result = await api.parentLinkToken();
    return { code: result.code, expires: result.expires_at };
  } catch (error) {
    return { error: (error && error.message) || 'Не удалось получить код.' };
  }
}

export async function linkParent(code) {
  if (!serverSession) return 'Привязка работает только на сервере.';
  try {
    await api.linkParent(code);
    const data = await server.load();
    adopt(data.profile);
    return '';
  } catch (error) {
    return (error && error.message) || 'Не удалось привязать ребёнка.';
  }
}

// Ключ колонки: у одной даты бывает несколько уроков (две алгебры подряд).
// slot 0 не меняет ключ — старые данные и демо остаются совместимыми.
export const lessonKey = (l) => l.date + (Number(l.slot) ? '#' + Number(l.slot) : '');

export function gradesOf(sid) {
  const s = studentById(sid);
  if (!s) return [];
  const js = allJournalKeys().filter((k) => k.cls === s.cls).map((k) => journal(k.key, true));
  const covered = new Set(js.map((j) => j.subjectKey));
  const list = (GRADES[sid] || []).filter(([, subj]) => !covered.has(subj)).map(([date, subj, value, w, type, comment], i) => ({ id: sid + '|' + subj + '|' + date + '|' + i, date, subj, value: normalizedScore(value), w, type, comment }));
  js.forEach((j) => {
    const r = j.names.indexOf(s.name);
    if (r < 0) return;
    j.lessons.forEach((l) => {
      const key = lessonKey(l);
      const value = j.grades[r + '|' + key];
      const t = typeOf(l.type);
      if (typeof value === 'number') list.push({ id: sid + '|' + j.key + '|' + key, date: l.date, subj: j.subjectKey, value, w: t.w, type: t.name, comment: j.comments[r + '|' + key] || '' });
    });
  });
  return list.sort((a, b) => a.date.localeCompare(b.date));
}

export function week(offset) {
  const s = student();
  if (!s) return { mon: new Date(), days: [] };
  const base = serverSession ? new Date() : NOW;
  const mon = addDays(mondayOf(base), offset * 7);
  const hw = HOMEWORK[s.id] || [];
  const grades = gradesOf(s.id);
  const absent = ABSENT[s.id] || [];
  const days = [];
  const nDays = 7;
  for (let i = 0; i < nDays; i++) {
    const d = addDays(mon, i);
    const date = iso(d);
    const holiday = holidayOn(d);
    if (holiday) { days.push({ date, d, off: holiday[0] }); continue; }
    const cls = classKey(s.cls), plan = scheduleOf(cls)[i] || [];
    const att = attendance(cls, date), snapshots = state.attendanceSubjects?.[cls + '|' + date] || {};
    const row = (classes()[cls] || { students: [] }).students.findIndex((x) => x.name === s.name);
    if (date < iso(base)) Object.entries(snapshots).forEach(([key, subj]) => {
      if (key.startsWith(row + '|') && att[key] && subj) plan[Number(key.split('|')[1]) - 1] = subj;
    });
    if (!plan.some(Boolean)) { days.push({ date, d, off: 'Выходной' }); continue; }
    const seenSubj = {};
    // В расписании бывают «окна» (null) — номер урока при этом сохраняется.
    const lessons = plan.map((subj, n) => {
      if (!subj) return null;
      const first = !seenSubj[subj];
      seenSubj[subj] = true;
      const h = hw.find((x) => x[0] === date && x[1] === subj);
      const jk = allJournalKeys().find((k) => k.cls === s.cls && k.subjectKey === subj);
      const j = jk ? journal(jk.key, true) : null;
      const jl = j?.lessons.find((l) => l.date === date);
      const jr = j ? j.names.indexOf(s.name) : -1;
      const key = s.id + '|' + date + '|' + subj;
      return {
        n: n + 1, subj, name: SUBJECTS[subj].name, room: SUBJECTS[subj].room, time: lessonTimes(i)[n] || '', start: bells(i)[n]?.start || '', end: bells(i)[n]?.end || '',
        hw: first ? (jl ? jl.hw : j?.deletedDates?.includes(date) ? '' : h ? h[2] : '') : '', hwKey: key, done: !!state.hwDone[key],
        grades: first ? grades.filter((g) => g.date === date && g.subj === subj) : [],
        absent: (first && (jr >= 0 && j?.lessons.some((l) => l.date === date && j.grades[jr + '|' + lessonKey(l)] === 'Н') || absent.some((a) => a[0] === date && a[1] === subj))) || (!!att[row + '|' + (n + 1)] && (!snapshots[row + '|' + (n + 1)] || snapshots[row + '|' + (n + 1)] === subj))
      };
    }).filter(Boolean);
    days.push({ date, d, lessons });
  }
  return { mon, days };
}

export function toggleHw(key, value) {
  const hwDone = Object.assign({}, state.hwDone);
  if (value) hwDone[key] = true; else delete hwDone[key];
  if (serverSession) {
    const id = server.taskId(key);
    if (id) (value ? api.completeTask(id) : api.updateTask(id, { status: 'pending' })).catch(() => {});
  }
  return set({ hwDone });
}

export function nextLesson() {
  const { days } = week(0);
  const base = serverSession ? new Date() : NOW;
  const nowMin = base.getHours() * 60 + base.getMinutes();
  const toMin = (t) => { const [h, m] = t.split(':').map(Number); return h * 60 + m; };
  const today = days.find((d) => d.date === iso(base));
  if (today && today.lessons) {
    const idx = today.lessons.findIndex((l) => toMin(l.end) > nowMin);
    if (idx >= 0) {
      const l = today.lessons[idx];
      const started = toMin(l.start) <= nowMin;
      return { lesson: l, after: today.lessons[idx + 1], inMin: toMin(l.start) - nowMin, started, today: true };
    }
  }
  return null;
}

export function tomorrowHomework() {
  const base = serverSession ? new Date() : NOW;
  for (let off = 1; off <= 7; off++) {
    const d = addDays(base, off);
    const w = week(Math.round((mondayOf(d) - mondayOf(base)) / (7 * 864e5)));
    const day = w.days.find((x) => x.date === iso(d));
    if (day && day.lessons) return { day, items: day.lessons.filter((l) => l.hw) };
    if (day && day.off && day.off !== 'Выходной') return { day, items: [] };
  }
  return null;
}

export function subjects(periodTo = currentPeriod().to, periodFrom = currentPeriod().from) {
  const s = student();
  const all = gradesOf(s.id).filter((g) => (!periodTo || g.date <= periodTo) && (!periodFrom || g.date >= periodFrom));
  const keys = [];
  scheduleOf(classKey(s.cls)).flat().filter(Boolean).forEach((k) => { if (!keys.includes(k)) keys.push(k); });
  return keys.map((k) => {
    const list = all.filter((g) => g.subj === k).sort((a, b) => a.date.localeCompare(b.date));
    const cls = classKey(s.cls), names = [];
    scheduleOf(cls).forEach((day, d) => day.forEach((subj, n) => {
      if (subj !== k) return;
      const explicit = lessonTeacher(cls, d, n);
      const name = explicit ? teachers().find((t) => t.id === explicit)?.name : teacherFor(k, cls);
      if (name && !names.includes(name)) names.push(name);
    }));
    return { key: k, name: SUBJECTS[k].name, teacher: names.join(', ') || (serverSession ? '' : SUBJECTS[k].teacher) || 'Учитель не назначен', list, avg: weighted(list) };
  });
}

// 8А по алгебре и геометрии — из журнала, остальное — тестовое распределение.
export const STATS_MIN = 5;
export function classStats(g) {
  const s = student();
  const key = classKey(s.cls);
  const size = ((classes()[key] || {}).students || []).length || 10;
  let values = null;
  const jKey = { algebra: '8a-algebra', geometry: '8a-geometry' }[g.subj];
  if (key === '8a' && jKey) {
    const j = journal(jKey, true);
    const cells = j.lessons.filter((l) => l.date === g.date).map(lessonKey);
    if (cells.length) values = j.names.map((_, r) => cells.map((c) => j.grades[r + '|' + c]).filter((v) => typeof v === 'number')).flat();
    if (values && !values.length) values = null;
  }
  if (!values) {
    let seed = [...g.id].reduce((a, ch) => a + ch.charCodeAt(0), 0);
    const rnd = () => { seed = (seed * 9301 + 49297) % 233280; return seed / 233280; };
    const n = size - (seed % 3 === 0 ? 1 : 0);
    values = [g.value];
    for (let i = 1; i < n; i++) values.push(Math.max(15, Math.min(100, Math.round(g.value + (rnd() - 0.5) * 50))));
  }
  const buckets = [0, 0, 0, 0];
  values.forEach((v) => { buckets[mark(v) - 2]++; });
  return { count: values.length, size, buckets, avg: values.reduce((a, b) => a + b, 0) / values.length };
}

export function newGrades() {
  const s = student();
  const seen = state.seen[state.session.login + '|' + s.id] || [];
  return gradesOf(s.id).filter((g) => g.date >= '2026-10-19' && !seen.includes(g.id)).sort((a, b) => b.date.localeCompare(a.date));
}
export function markSeen() {
  const s = student();
  if (!s) return false;
  const seen = Object.assign({}, state.seen, { [state.session.login + '|' + s.id]: gradesOf(s.id).map((g) => g.id) });
  if (JSON.stringify(seen) === JSON.stringify(state.seen)) return true;
  return set({ seen });
}

export const person = (id) => serverSession
  ? server.person(id)
  : people().find((p) => p.id === id) || state.archivedPeople?.[id] || PEOPLE.find((p) => p.id === id) || { name: id, role: '' };
export const people = () => serverSession ? server.session.people : PEOPLE.filter((p) => !p.id.startsWith('t-')).concat(teachers().map((t) => {
  const homerooms = Object.values(classes()).filter((c) => c.teacher === t.id).map((c) => c.name);
  const subjects = [...new Set(t.load.map(([key]) => SUBJECTS[key]?.name || key))];
  return { id: t.id, name: t.name, role: [homerooms.length ? 'Классный руководитель ' + homerooms.join(', ') : 'Учитель', ...subjects].join(' · ') };
}));
const deleted = (m) => !!(state.deletedMail && state.deletedMail[m.id]);
export function inbox() {
  if (serverSession) return server.inbox();
  return INBOX[role()].filter((m) => !deleted(m)).map((m) => Object.assign({}, m, { unread: m.unread && !state.readMail[m.id] }));
}
export function sent() {
  if (serverSession) return server.sent();
  const own = (state.sent[role()] || []);
  return own.concat(SENT[role()]).filter((m) => !deleted(m)).sort((a, b) => b.date.localeCompare(a.date));
}
export const deleteMail = (id) => serverSession ? server.deleteMail(id) : set({ deletedMail: Object.assign({}, state.deletedMail, { [id]: true }) });
export const unreadCount = () => inbox().filter((m) => m.unread).length;
export const markRead = (id) => serverSession ? server.markRead(id) : set({ readMail: Object.assign({}, state.readMail, { [id]: true }) });
export function send(msg) {
  if (serverSession) return server.sendMail(msg);
  const list = (state.sent[role()] || []).slice();
  list.unshift(Object.assign({ id: 'my' + Date.now(), date: iso(NOW) + 'T' + NOW.toTimeString().slice(0, 5) }, msg));
  return set({ sent: Object.assign({}, state.sent, { [role()]: list }) });
}
export async function uploadAttachment(file) {
  if (!serverSession) return { name: file.name, token: '' };
  return api.uploadAttachment(file);
}
export function deleteAttachment(fileToken) {
  // Уборка best-effort: если сервер не ответил, файл снимет ночная чистка.
  if (!serverSession || !fileToken) return Promise.resolve();
  return api.deleteAttachment(fileToken).catch(() => {});
}
export function downloadAttachment(fileToken, name) {
  if (!serverSession || !fileToken) return Promise.reject(new Error('Файл недоступен'));
  return api.downloadAttachment(fileToken).then((blob) => {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url; link.download = name || 'файл';
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    return true;
  });
}

export function journal(key, allPeriods = false, normalize = true) {
  if (serverSession) {
    const source = server.session.journals[key];
    if (!source) throw new Error('Неизвестный журнал: ' + key);
    const result = JSON.parse(JSON.stringify(source));
    const p = currentPeriod();
    result.periodKey = p.key;
    result.periodFrom = p.from;
    result.periodTo = p.to;
    result.finals = (result.finalsByPeriod && result.finalsByPeriod[p.key]) || {};
    return result;
  }
  const own = state.journals && state.journals[key];
  const j = JOURNALS[key];
  const meta = allJournalKeys().find((k) => k.key === key);
  if (!meta) throw new Error('Неизвестный журнал: ' + key);
  const result = own ? JSON.parse(JSON.stringify(own)) : j ? {
    key, cls: j.cls, subj: j.subj, names: j.names.slice(),
    lessons: j.dates.map((d, i) => ({ date: d, slot: 0, type: j.types[i], hw: j.hw[i] || '' })),
    grades: Object.fromEntries(j.rows.flatMap((row, r) => row.map((v, c) => [r + '|' + j.dates[c], v]).filter(([, v]) => v !== ''))),
    comments: Object.fromEntries(Object.entries(j.comments).map(([k, v]) => { const [r, c] = k.split('|'); return [r + '|' + j.dates[c], v]; })),
    finals: {}
  } : { key, cls: meta.cls, subj: meta.subj, names: [], lessons: [], grades: {}, comments: {}, finals: {} };
  result.subjectKey = meta.subjectKey;
  const roster = classes()[classKey(meta.cls)]?.students.map((s) => s.name) || [];
  const remap = (map) => Object.fromEntries(Object.entries(map || {}).flatMap(([k, v]) => {
    const [r, ...rest] = k.split('|');
    const index = roster.indexOf(result.names[Number(r)]);
    return index < 0 ? [] : [[index + (rest.length ? '|' + rest.join('|') : ''), v]];
  }));
  result.grades = remap(result.grades);
  result.comments = remap(result.comments);
  const legacyFinals = !result.finalsByPeriod ? remap(result.finals) : null;
  result.finalsByPeriod = Object.fromEntries(Object.entries(result.finalsByPeriod || {}).map(([p, m]) => [p, remap(m)]));
  if (legacyFinals && Object.keys(legacyFinals).length) result.finalsByPeriod['quarters|1 четверть'] = Object.fromEntries(Object.entries(legacyFinals).map(([r, v]) => [r, v * 20]));
  result.names = roster;
  if (normalize) result.grades = Object.fromEntries(Object.entries(result.grades).map(([k, v]) => [k, normalizedScore(v)]));
  const p = currentPeriod();
  result.finals = result.finalsByPeriod[p.key] || {};
  if (!allPeriods) { result.lessons = result.lessons.filter((l) => l.date >= p.from && l.date <= p.to); result.periodKey = p.key; result.periodFrom = p.from; result.periodTo = p.to; }
  journalSnapshots.set(result, { generation, value: JSON.parse(JSON.stringify(result)) });
  return result;
}
export function saveJournal(j, retry = true) {
  refresh();
  if (serverSession) {
    if (!server.session.journalKeys.some((k) => k.key === j.key)) return false;
    const full = Object.assign({}, j);
    full.finalsByPeriod = Object.assign({}, j.finalsByPeriod, { [j.periodKey || currentPeriod().key]: j.finals });
    return server.saveJournal(full);
  }
  if (!account() || (role() === 'teacher' && !journalKeys().some((k) => k.key === j.key))) return false;
  const snapshot = journalSnapshots.get(j)?.value;
  const current = journal(j.key, !j.periodKey);
  if (JSON.stringify(current.names) !== JSON.stringify(j.names) || current.periodKey !== j.periodKey) return false;
  // Меняем только затронутые ячейки. Конкурирующая правка той же ячейки требует повтора.
  if (snapshot) {
    for (const field of ['grades', 'comments', 'finals']) {
      for (const k of new Set([...Object.keys(snapshot[field]), ...Object.keys(j[field])])) {
        if (JSON.stringify(snapshot[field][k]) === JSON.stringify(j[field][k])) continue;
        if (JSON.stringify(current[field][k]) !== JSON.stringify(snapshot[field][k]) && JSON.stringify(current[field][k]) !== JSON.stringify(j[field][k])) return false;
        if (k in j[field]) current[field][k] = j[field][k]; else delete current[field][k];
      }
    }
    if (JSON.stringify(j.lessons) !== JSON.stringify(snapshot.lessons)) {
      if (JSON.stringify(current.lessons) !== JSON.stringify(snapshot.lessons)) return false;
      current.lessons = j.lessons;
    }
  } else Object.assign(current, j);
  const edited = j;
  j = current;
  const full = journal(j.key, true, false);
  const outside = (date) => j.periodKey && (date < j.periodFrom || date > j.periodTo);
  const merge = (old, cur) => Object.assign(Object.fromEntries(Object.entries(old).filter(([k]) => outside(k.split('|')[1].split('#')[0]))), cur);
  // shortcut: в демо «удалённые даты» хранятся без slot, при двух уроках дня
  // пропадёт подсказка ДЗ за весь день. На серверный режим не влияет.
  const deletedDates = [...new Set([...(full.deletedDates || []), ...full.lessons.filter((l) => !outside(l.date) && !j.lessons.some((x) => lessonKey(x) === lessonKey(l))).map((l) => l.date)])].filter((d) => !j.lessons.some((l) => l.date === d));
  const result = { ...j, deletedDates, lessons: [...full.lessons.filter((l) => outside(l.date)), ...j.lessons].sort((a, b) => a.date.localeCompare(b.date) || (Number(a.slot) || 0) - (Number(b.slot) || 0)), grades: merge(full.grades, j.grades), comments: merge(full.comments, j.comments), finalsByPeriod: { ...full.finalsByPeriod, [j.periodKey || currentPeriod().key]: j.finals } };
  // Смена шкалы округляет только показ; нетронутые исходные баллы не теряют точность.
  Object.keys(result.grades).forEach((k) => { if (result.grades[k] === normalizedScore(full.grades[k])) result.grades[k] = full.grades[k]; });
  delete result.periodKey; delete result.periodFrom; delete result.periodTo;
  const before = generation;
  const ok = set({ journals: Object.assign({}, state.journals, { [j.key]: result }) });
  if (ok) journalSnapshots.set(edited, { generation, value: JSON.parse(JSON.stringify(edited)) });
  else if (retry && generation !== before) return saveJournal(edited, false);
  return ok;
}
export const journalKeys = () => serverSession ? server.session.journalKeys : account() ? allJournalKeys().filter((k) => role() !== 'teacher' || teachers().some((t) => t.id === account()?.teacherId && t.load.some(([s, cs]) => s === k.subjectKey && cs.includes(classKey(k.cls))))) : [];

export function workTypes() {
  if (serverSession) return server.session.workTypes.length ? server.session.workTypes : WORK_TYPES.map((t) => Object.assign({}, t));
  return state.workTypes || WORK_TYPES.map((t) => Object.assign({}, t));
}
export function setWorkTypes(list) { return serverSession ? server.setWorkTypes(list) : set({ workTypes: list }); }
export const workTypeUsage = (id) => allJournalKeys().reduce((n, k) => n + journal(k.key, true).lessons.filter((l) => l.type === id).length, 0);
export function removeWorkType(id) {
  if (id === 'lesson') return false;
  if (serverSession) return server.removeWorkType(id);
  const journals = { ...state.journals };
  allJournalKeys().forEach((k) => {
    const j = journal(k.key, true, false);
    if (j.lessons.some((l) => l.type === id)) {
      j.lessons.forEach((l) => { if (l.type === id) l.type = 'lesson'; });
      journals[k.key] = j;
    }
  });
  return set({ journals, workTypes: workTypes().filter((t) => t.id !== id) });
}
export const typeOf = (id) => workTypes().find((t) => t.id === id) || { id, name: 'Работа на уроке', w: 1 };

export function rowAvg(j, r) {
  let s = 0, w = 0;
  j.lessons.forEach((l) => {
    const v = j.grades[r + '|' + lessonKey(l)];
    if (typeof v === 'number') { const k = typeOf(l.type).w; s += v * k; w += k; }
  });
  return w ? s / w : null;
}

const CLASS_KEYS = { '8А': '8a', '8Б': '8b', '7В': '7v', '4Б': '4b' };
export const classKey = (name) => CLASS_KEYS[name] || (serverSession ? server.classKey(name) : name);
const copy = (o) => JSON.parse(JSON.stringify(o));

export const classes = () => copy(serverSession ? CLASSES : state.classes || CLASSES);
export const saveClasses = (c) => set({ classes: c });
export const teachers = () => copy(serverSession ? TEACHERS : state.teachers || TEACHERS);
export const saveTeachers = (t) => set({ teachers: t });

// --- Задачи ---------------------------------------------------------------

const taskGuard = (fn) => serverSession ? fn() : Promise.reject(new Error('Задачи доступны только на сервере.'));
export const taskList = () => taskGuard(() => server.taskList());
export const taskCreate = (body) => taskGuard(() => server.taskCreate(body));
export const taskUpdate = (id, body) => taskGuard(() => server.taskUpdate(id, body));
export const taskDelete = (id) => taskGuard(() => server.taskDelete(id));
export const taskComplete = (id) => taskGuard(() => server.taskComplete(id));
export const taskSubtasks = (id) => taskGuard(() => server.taskSubtasks(id));
export const taskAiSubtasks = (id) => taskGuard(() => server.taskAiSubtasks(id));

// --- Задания класса (учитель) --------------------------------------------

export const assignmentList = () => serverSession ? server.assignmentList() : Promise.reject(new Error('Задания доступны только на сервере.'));
export const assignmentCreate = (body) => serverSession ? server.assignmentCreate(body) : Promise.reject(new Error('Задания доступны только на сервере.'));
export const assignmentDelete = (id) => serverSession ? server.assignmentDelete(id) : Promise.reject(new Error('Задания доступны только на сервере.'));
export const assignmentProgress = (id) => serverSession ? server.assignmentProgress(id) : Promise.reject(new Error('Задания доступны только на сервере.'));
export const assignmentGrades = (id) => serverSession ? server.assignmentGrades(id) : Promise.reject(new Error('Задания доступны только на сервере.'));
export const assignmentGrade = (id, subject, items) => serverSession ? server.assignmentGrade(id, subject, items) : Promise.reject(new Error('Задания доступны только на сервере.'));
export const assignmentAccept = (id, body) => serverSession ? server.assignmentAccept(id, body) : Promise.reject(new Error('Задания доступны только на сервере.'));
export const classWorkload = (classId, date) => serverSession ? server.classWorkload(classId, date) : Promise.resolve(null);

// --- Статистика (сервер) --------------------------------------------------
// Родитель смотрит своего ребёнка, ученик — себя. Демо-режима нет:
// цифры должны быть настоящими.

const statsTarget = () => (serverSession && serverSession.role === 'parent' ? serverSession.childId : null);
const statsGuard = (run) => serverSession ? run() : Promise.reject(new Error('Статистика доступна только на сервере.'));
export const statsDaily = () => statsGuard(() => server.statsDaily(statsTarget()));
export const statsWeekly = () => statsGuard(() => server.statsWeekly(statsTarget()));
export const statsDynamics = (weeks) => statsGuard(() => server.statsDynamics(statsTarget(), weeks));
export const statsInsight = () => statsGuard(() => server.statsInsight(statsTarget()));

// --- Управление классами и учителями (сервер) -----------------------------

export const apiCreateClass = (name, teacherId) => server.createClass(name, teacherId);
export const apiPatchClass = (key, patch) => server.patchClass(key, patch);
export const apiDeleteClass = (key) => server.deleteClass(key);
export const apiAddStudent = (key, studentId) => server.addStudentToClass(key, studentId);
export const apiRemoveStudent = (key, studentId) => server.removeStudentFromClass(key, studentId);
export const apiTeacherInvite = () => server.createTeacherInvite();
export const apiInviteCodes = () => server.inviteCodes();
export const apiDeleteInvite = (id) => server.deleteInviteCode(id);
export const apiActivateUser = (id, active = true) => server.activateUser(id, active);
export const apiSetTeacherSubjects = (id, subjects) => server.setTeacherSubjects(id, subjects);
export const apiDeactivateTeacher = (id) => server.deactivateTeacher(id);
// Перенос учеников: JSON-файл по строгой схеме (экспорт/импорт в панели).
export const apiExportStudents = () => server.exportStudents();
export const apiImportStudents = (doc) => server.importStudents(doc);

export function unassignedStudents(clsKey) {  if (!serverSession) return [];
  return (server.session.students || [])
    .filter((s) => s.class_id === null || String(s.class_id) !== String(clsKey))
    .map((s) => ({ id: String(s.id), name: [s.last_name, s.first_name].filter(Boolean).join(' ') || s.email || 'Ученик', cls: s.class_name || '' }));
}
export function addTeacher(value) {
  if (role() !== 'admin') return { error: 'Добавлять учителей может только администратор.' };
  const name = String(value).trim().replace(/\s+/g, ' ');
  if (name.split(' ').length < 2 || name.length > 120) return { error: 'Введите фамилию и имя, не больше 120 символов.' };
  const teacher = { id: 't-' + crypto.randomUUID(), name, login: uniqueLogin(name), load: [] };
  if (!saveTeachers([...teachers(), teacher])) return { error: SAVE_ERROR };
  const invite = invitation(teacher.login);
  return invite ? { teacher, invitation: invite } : { error: SAVE_ERROR };
}
export function deleteTeacher(id) {
  if (role() !== 'admin') return 'Удалять учителей может только администратор.';
  const list = teachers(), teacher = list.find((t) => t.id === id);
  if (!teacher) return 'Учитель уже удалён. Обновите список.';
  const login = teacherLogin(teacher), all = classes(), assignments = copy(state.schedTeachers || {});
  Object.values(all).forEach((c) => { if (c.teacher === id) c.teacher = ''; });
  Object.values(assignments).forEach((slots) => Object.keys(slots).forEach((slot) => { if (slots[slot] === id) delete slots[slot]; }));
  const patch = { teachers: list.filter((t) => t.id !== id), classes: all, schedTeachers: assignments, retiredLogins: [...new Set([...(state.retiredLogins || []), login])], archivedPeople: { ...state.archivedPeople, [id]: person(id) } };
  for (const field of ['accounts', 'invitations', 'passwords', 'maxDone', 'avatars']) {
    patch[field] = { ...state[field] }; delete patch[field][login];
  }
  return set(patch) ? '' : SAVE_ERROR;
}
export function removeScheduleLesson(cls, day, index) {
  if (role() !== 'admin') return 'Менять расписание может только администратор.';
  if (!classes()[cls] || !Number.isInteger(day) || day < 0 || day > 6 || !Number.isInteger(index) || index < 0) return 'Урок не найден.';
  if (serverSession) {
    const plan = scheduleOf(cls);
    if (!plan[day][index]) return 'Урок уже удалён.';
    server.removeLesson(cls, day, index);
    return '';
  }
  const plan = scheduleOf(cls);
  if (!plan[day][index]) return 'Урок уже удалён.';
  plan[day][index] = null;
  while (plan[day].length && !plan[day].at(-1)) plan[day].pop();
  const assignments = copy(state.schedTeachers || {});
  if (assignments[cls]) delete assignments[cls][day + '|' + index];
  return set({ schedules: { ...state.schedules, [cls]: plan }, schedTeachers: assignments, ...attendanceForSchedule(cls, plan) }) ? '' : SAVE_ERROR;
}
export function scheduleOf(key) {
  let plan = copy(serverSession ? (SCHEDULES[key] || []) : (state.schedules?.[key] || SCHEDULES[key] || []));
  // Сервер кладёт расписание под сырое название «8А», а classKey даёт «8a»:
  // «а» кириллицей и «a» латиницей визуально неразличимы, но ключи разные — ищем по нормализации.
  if (serverSession && !plan.some((d) => d?.length)) {
    const norm = (s) => String(s || '').toLowerCase().replace(/а/g, 'a').replace(/б/g, 'b').replace(/в/g, 'v').replace(/г/g, 'g');
    const found = Object.keys(SCHEDULES).find((k) => norm(k) === norm(key) && SCHEDULES[k]?.length);
    if (found) plan = copy(SCHEDULES[found]);
  }
  while (plan.length < 7) plan.push([]);
  return plan;
}
function attendanceForSchedule(cls, plan) {
  const previous = scheduleOf(cls), all = copy(state.attendance || {}), subjects = copy(state.attendanceSubjects || {});
  // «Н» относится к удалённому занятию, а не к следующему предмету на том же номере.
  Object.entries(all).forEach(([key, marks]) => {
    if (!key.startsWith(cls + '|')) return;
    const day = weekday(parse(key.slice(cls.length + 1)));
    const past = key.slice(cls.length + 1) < iso(NOW);
    subjects[key] ||= {};
    Object.keys(marks).forEach((mark) => {
      const n = Number(mark.split('|')[1]) - 1;
      if (past) {
        // Миграция старых булевых отметок до первого изменения расписания.
        subjects[key][mark] ||= previous[day]?.[n];
      } else if (!plan[day]?.[n] || plan[day][n] !== previous[day]?.[n]) { delete marks[mark]; delete subjects[key][mark]; }
    });
  });
  return { attendance: all, attendanceSubjects: subjects };
}
export const saveSchedule = (key, s) => set({ schedules: Object.assign({}, state.schedules, { [key]: copy(s) }), ...attendanceForSchedule(key, s) });
export function scheduleAssignmentError(subj, cls, teacherId, day, index) {
  if (!subj) return '';
  if (!SUBJECTS[subj]) return 'Выберите предмет из списка.';
  if (teacherId ? !teachers().some((t) => t.id === teacherId) : !teacherFor(subj, cls)) return 'Выберите учителя для этого урока.';
  if (day != null && index != null) {
    const list = teachers();
    const teacher = teacherId ? list.find((t) => t.id === teacherId) : list.find((t) => t.name === teacherFor(subj, cls));
    const other = Object.values(classes()).find((c) => {
      if (c.key === cls) return false;
      const subject = scheduleOf(c.key)[day]?.[index];
      if (!subject) return false;
      const assigned = lessonTeacher(c.key, day, index);
      return assigned ? assigned === teacher.id : teacherFor(subject, c.key) === teacher.name;
    });
    if (other) return teacher.name + ' в это время ведёт ' + other.name + '.';
  }
  return '';
}
export const lessonTeacher = (key, d, n) => serverSession
  ? ((server.session.schedTeachers[key] || {})[d + '|' + n] || '')
  : ((state.schedTeachers || {})[key] || {})[d + '|' + n] || '';
export function saveScheduleLesson(key, plan, d, n, teacherId) {
  if (serverSession) {
    const time = bells(d)[n];
    const subject = plan?.[d]?.[n];
    return server.saveLesson(key, plan, d, n, teacherId, time, subject ? SUBJECTS[subject]?.room || '' : '');
  }
  const all = Object.assign({}, state.schedTeachers);
  const assignments = Object.assign({}, all[key]);
  if (teacherId) assignments[d + '|' + n] = teacherId; else delete assignments[d + '|' + n];
  all[key] = assignments;
  return set({ schedules: Object.assign({}, state.schedules, { [key]: copy(plan) }), schedTeachers: all, ...attendanceForSchedule(key, plan) });
}
export function setLessonTeacher(key, d, n, teacherId) {
  const all = Object.assign({}, state.schedTeachers);
  const m = Object.assign({}, all[key]);
  if (teacherId) m[d + '|' + n] = teacherId; else delete m[d + '|' + n];
  all[key] = m;
  return set({ schedTeachers: all });
}
export function school() {
  const s = serverSession
    ? Object.assign(copy(SCHOOL_SETTINGS), copy(server.session.school || {}))
    : copy(state.school || SCHOOL_SETTINGS);
  // Старые сохранения без зимних/весенних каникул — берём полный список периодов по умолчанию.
  Object.keys(SCHOOL_SETTINGS.periods).forEach((k) => {
    if (!s.periods[k] || s.periods[k].length < SCHOOL_SETTINGS.periods[k].length) s.periods[k] = copy(SCHOOL_SETTINGS.periods[k]);
  });
  if (!s.bounds) s.bounds = {};
  s.days = 7;
  if (!s.bells) s.bells = DEFAULT_TIMES.map((t) => { const [start, end] = t.split('–'); return { start: start.padStart(5, '0'), end: end.padStart(5, '0') }; });
  if (!s.bellSets?.length) s.bellSets = [{ id: 'main', name: 'Основной', bells: s.bells }];
  s.bellDays = Array.from({ length: 7 }, (_, d) => s.bellSets.some((b) => b.id === s.bellDays?.[d]) ? s.bellDays[d] : s.bellSets[0].id);
  s.bells = s.bellSets[0].bells;
  return s;
}
export const bellSetFor = (day = 0, s = school()) => s.bellSets.find((b) => b.id === s.bellDays[typeof day === 'number' ? day : weekday(typeof day === 'string' ? parse(day) : day)]) || s.bellSets[0];
export const bells = (day = 0) => serverSession && server.session.times?.[day]?.length
  ? server.session.times[day]
  : bellSetFor(day).bells;
export const lessonTimes = (day = 0) => bells(day).map(({ start, end }) => start + '–' + end);
export function bellTimeIssue(list) {
  for (let i = 0; i < list.length; i++) {
    const { start, end } = list[i];
    const field = !/^([01]\d|2[0-3]):[0-5]\d$/.test(start) ? 'start' : !/^([01]\d|2[0-3]):[0-5]\d$/.test(end) ? 'end' : null;
    if (field) return { row: i, field, message: 'Урок ' + (i + 1) + ': укажите ' + (field === 'start' ? 'начало' : 'конец') + ' от 00:00 до 23:59.' };
    if (start >= end) return { row: i, field: 'end', message: 'Урок ' + (i + 1) + ': конец должен быть позже начала (' + start + ').' };
    if (i && list[i - 1].end > start) return { row: i, field: 'start', overlap: true, message: 'Уроки ' + i + ' и ' + (i + 1) + ' пересекаются: предыдущий заканчивается в ' + list[i - 1].end + ', следующий начинается в ' + start + '.' };
  }
  return null;
}
export function bellsError(list, days = [0, 1, 2, 3, 4, 5, 6]) {
  if (!list.length) return 'Добавьте хотя бы один урок.';
  const issue = bellTimeIssue(list);
  if (issue) return issue.message;
  const needed = Math.max(0, ...Object.keys(classes()).flatMap((k) => scheduleOf(k).filter((_, d) => days.includes(d)).map((d) => d.reduce((n, s, i) => s ? i + 1 : n, 0))));
  return list.length < needed ? 'В расписании есть урок ' + needed + '. Сначала уберите его из расписания.' : '';
}
export function bellSetsError(s) {
  if (!s.bellSets?.length) return 'Добавьте набор звонков.';
  if (new Set(s.bellSets.map((b) => b.id)).size !== s.bellSets.length || s.bellSets.some((b) => !b.id || !b.name.trim())) return 'Укажите название каждого набора.';
  if (s.bellDays?.length !== 7 || s.bellDays.some((id) => !s.bellSets.some((b) => b.id === id))) return 'Выберите набор для каждого дня.';
  for (const b of s.bellSets) {
    const error = bellsError(b.bells, s.bellDays.flatMap((id, d) => id === b.id ? [d] : []));
    if (error) return b.name + ': ' + error;
  }
  return '';
}
export function appendBell(day = 0) {
  const s = school(), list = bellSetFor(day, s).bells, last = list.at(-1).end.split(':').map(Number), start = last[0] * 60 + last[1] + 10;
  if (start + 40 >= 1440) return 'Новый урок не помещается в сутки. Измените звонки в настройках.';
  const time = (v) => String(Math.floor(v / 60)).padStart(2, '0') + ':' + String(v % 60).padStart(2, '0');
  list.push({ start: time(start), end: time(start + 40) }); return saveSchool(s);
}
export function removeLastBell(day = 0) {
  if (role() !== 'admin') return 'Удалять уроки может только администратор.';
  const s = school(), b = bellSetFor(day, s), next = b.bells.slice(0, -1);
  const days = s.bellDays.flatMap((id, d) => id === b.id ? [d] : []), error = bellsError(next, days);
  if (error) return error;
  const assignments = copy(state.schedTeachers || {});
  Object.values(assignments).forEach((slots) => Object.keys(slots).forEach((key) => { const [d, n] = key.split('|').map(Number); if (days.includes(d) && n >= next.length) delete slots[key]; }));
  b.bells = next; s.bells = s.bellSets[0].bells;
  if (serverSession) return server.saveSchool(s) ? '' : SAVE_ERROR;
  return set({ schedTeachers: assignments, school: s }) ? '' : SAVE_ERROR;
}
export function boundsFor(s, scale) {
  if (s.bounds && s.bounds[scale]) return s.bounds[scale];
  if (scale === 5) return [3, 4, 5];
  return [0.4, 0.6, 0.8].map((p) => Math.ceil(scale * p));
}
export const avatarOf = (login) => serverSession
  ? (server.session.profile?.avatar_url || '')
  : (state.avatars && state.avatars[login || (state.session && state.session.login)]) || '';
export function setAvatar(id) {
  if (serverSession) {
    server.saveProfile({ avatar_url: id || null }).then((profile) => {
      if (!profile) return;
      serverAccount = server.accountFor(profile, server.session.children);
      dataListeners.forEach((render) => render(false, []));
    });
    return true;
  }
  return set({ avatars: Object.assign({}, state.avatars, { [state.session.login]: id }) });
}
// Имя и фамилия: в серверном режиме уходят в PATCH /api/users/me.
export function updateProfile(fields) {
  if (serverSession) {
    server.saveProfile(fields).then((profile) => {
      if (!profile) return;
      serverAccount = server.accountFor(profile, server.session.children);
      dataListeners.forEach((render) => render(false, []));
    });
    return true;
  }
  return set({ profile: Object.assign({}, state.profile, fields) });
}
export function changePassword(current, next) {
  if (!serverSession) return 'Смена пароля доступна только на сервере.';
  return server.changePassword(current, next);
}
export function saveSchool(s) {
  if (s.bellSets) {
    if (!s.bellSets.length) return bellSetsError(s);
    // Старые редакторы ещё сохраняют основной набор через school.bells.
    if (s.bells && JSON.stringify(s.bells) !== JSON.stringify(school().bells) && JSON.stringify(s.bellSets[0].bells) === JSON.stringify(school().bells)) s.bellSets[0].bells = s.bells;
    s.bells = s.bellSets[0].bells;
    const error = bellSetsError(s);
    if (error) return error;
  }
  if (serverSession) {
    if (!server.saveSchool(copy(s))) return 'Не удалось сохранить настройки.';
    configureGrading(s.scale || 100, boundsFor(s, s.scale || 100));
    return '';
  }
  if (!set({ school: copy(s) })) return 'Данные изменились в другой вкладке. Повторите действие.';
  configureGrading(s.scale, boundsFor(s, s.scale));
  return '';
}

// --- ИИ -------------------------------------------------------------------

export async function askAi(message, options) {
  if (!serverSession) return null;
  try {
    return await api.aiChat(message, options);
  } catch {
    return null;
  }
}

export async function aiChats() {
  if (!serverSession) return [];
  try {
    const list = await api.aiChats();
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

export function aiDeleteChat(id) {
  if (!serverSession) return Promise.resolve(false);
  return api.aiDeleteChat(id).then(() => true).catch(() => false);
}

export function aiAction(body) {
  if (!serverSession) return Promise.resolve(null);
  return api.aiAction(body).catch(() => null);
}

export async function aiHistory(chatId) {
  if (!serverSession) return [];
  try {
    const list = await api.aiHistory(chatId);
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

// --- Уведомления ----------------------------------------------------------

export async function notifications() {
  if (!serverSession) return [];
  try {
    const list = await api.notifications();
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

export function readNotification(id) {
  if (!serverSession) return Promise.resolve(false);
  return api.readNotification(id).then(() => true).catch(() => false);
}

export function readAllNotifications() {
  if (!serverSession) return Promise.resolve(false);
  return api.readAllNotifications().then(() => true).catch(() => false);
}

export const attendance = (cls, date) => serverSession ? server.attendance(cls, date) : Object.assign({}, (state.attendance || {})[cls + '|' + date]);
export function setAttendance(cls, date, marks) {
  if (serverSession) return server.saveAttendance(cls, date, marks);
  const key = cls + '|' + date, plan = scheduleOf(cls)[weekday(parse(date))];
  const old = state.attendanceSubjects?.[key] || {};
  const subjects = Object.fromEntries(Object.keys(marks).map((k) => [k, old[k] || plan[Number(k.split('|')[1]) - 1]]));
  return set({ attendance: { ...state.attendance, [key]: copy(marks) }, attendanceSubjects: { ...state.attendanceSubjects, [key]: subjects } });
}

const schoolDay = (cls, d) => !holidayOn(d) && (scheduleOf(cls)[weekday(d)] || []).some(Boolean);
// Демо-пропуски прошлых дней: детерминированы, чтобы история не была пустой.
const hash = (s) => { let h = 2166136261; for (const c of s) h = Math.imul(h ^ c.charCodeAt(0), 16777619); return (h >>> 0) / 4294967296; };
function demoMissed(cls, date, name, plan) {
  const all = plan.map((s, n) => (s ? n + 1 : 0)).filter(Boolean);
  const x = hash(cls + date + name);
  if (x < 0.035) return all;
  if (x >= 0.07) return [];
  const i = Math.floor(hash(name + date) * all.length);
  return all.slice(i, i + (hash(date + name) < 0.4 ? 2 : 1));
}

// «Н» классного, «н» из журналов, демо-пропуски Артёма и Сони и прошлых дней.
export function absencesOn(cls, date) {
  const c = classes()[cls];
  const d = parse(date);
  if (!c) return [];
  const plan = scheduleOf(cls)[weekday(d)] || [];
  const att = attendance(cls, date);
  const snapshots = state.attendanceSubjects?.[cls + '|' + date] || {};
  if (!schoolDay(cls, d) && !Object.keys(att).length) return [];
  const today = iso(NOW);
  const js = allJournalKeys().filter((k) => k.cls === c.name).map((k) => journal(k.key, true));
  const seeded = serverSession ? {} : Object.fromEntries(Object.keys(ABSENT).map((id) => [STUDENTS[id].name, ABSENT[id]]));
  return c.students.map((s, r) => {
    const missed = new Set(Object.keys(att).filter((k) => k.startsWith(r + '|') && att[k] && (snapshots[k] || plan[Number(k.split('|')[1]) - 1])).map((k) => Number(k.split('|')[1])));
    js.forEach((j) => {
      const jr = j.names.indexOf(s.name);
      if (jr >= 0 && j.lessons.some((l) => l.date === date && j.grades[jr + '|' + lessonKey(l)] === 'Н')) { const n = plan.indexOf(j.subjectKey); if (n >= 0) missed.add(n + 1); }
    });
    if (seeded[s.name]) seeded[s.name].filter(([dt]) => dt === date).forEach(([, subj]) => { const n = plan.indexOf(subj); if (n >= 0) missed.add(n + 1); });
    else if (!serverSession && date < today) demoMissed(cls, date, s.name, plan).forEach((n) => missed.add(n));
    const lessons = [...missed].sort((a, b) => a - b).map((n) => ({ n, subj: snapshots[r + '|' + n] || plan[n - 1] }));
    return { name: s.name, lessons, allDay: lessons.length > 0 && lessons.length === plan.filter(Boolean).length };
  }).filter((x) => x.lessons.length);
}

export function absencesBetween(cls, from, to) {
  const c = classes()[cls];
  if (!c) return [];
  const sum = Object.fromEntries(c.students.map((s) => [s.name, { name: s.name, lessons: 0, days: 0 }]));
  for (let d = parse(from); iso(d) <= to; d = addDays(d, 1)) {
    absencesOn(cls, iso(d)).forEach((a) => { sum[a.name].lessons += a.lessons.length; if (a.allDay) sum[a.name].days++; });
  }
  return Object.values(sum).sort((a, b) => b.lessons - a.lessons || a.name.localeCompare(b.name, 'ru'));
}
export function schoolDaysOf(cls, from, to) {
  const out = [];
  for (let d = parse(from); iso(d) <= to; d = addDays(d, 1)) if (schoolDay(cls, d)) out.push(iso(d));
  return out;
}

// До первого дня после периода — так же считаются дни до каникул.
export const periodDaysLeft = () => Math.max(0, Math.round((addDays(parse(currentPeriod().to), 1) - parse(iso(NOW))) / 864e5));

export function nextHolidays() {
  if (serverSession) return null;
  const today = iso(NOW);
  const s = school();
  const h = s.periods[s.periodType].find(([name, from, to]) => /каникул/i.test(name) && to >= today);
  if (!h) return null;
  return { name: h[0], from: h[1], to: h[2], days: Math.max(0, Math.round((parse(h[1]) - parse(today)) / 864e5)), now: h[1] <= today };
}

export function teacherFor(subj, cls) {
  if (serverSession) return server.teacherFor(subj, cls);
  const t = teachers().find((x) => x.load.some(([s, list]) => s === subj && list.includes(cls)));
  return t ? t.name : '';
}

export function currentPeriod() {
  if (serverSession) {
    const d = new Date(NOW);
    const y = d.getMonth() >= 7 ? d.getFullYear() : d.getFullYear() - 1;
    return { name: 'Учебный год', from: y + '-09-01', to: (y + 1) + '-05-31', key: 'year' };
  }
  const s = school(), today = iso(NOW);
  const ps = s.periods[s.periodType].filter(([name]) => !/каникул/i.test(name));
  const p = ps.find(([, from, to]) => from <= today && to >= today) || ps.find(([, from]) => from > today) || ps[ps.length - 1];
  return { name: p[0], from: p[1], to: p[2], key: s.periodType + '|' + p[0] };
}
const PERIOD_WORDS = {
  quarters: { acc: 'четверть', gen: 'четверти', prep: 'четверти' },
  trimesters: { acc: 'триместр', gen: 'триместра', prep: 'триместре' },
  halves: { acc: 'полугодие', gen: 'полугодия', prep: 'полугодии' }
};
export const periodWord = (form = 'acc') => serverSession ? (form === 'acc' ? 'год' : form === 'gen' ? 'года' : 'году') : PERIOD_WORDS[school().periodType][form];
export const studyPeriods = () => serverSession ? [] : school().periods[school().periodType].filter(([n]) => !/каникул/i.test(n)).map(([name, from, to]) => ({ name, from, to, key: school().periodType + '|' + name }));
export const finalsComplete = (j) => j.names.length > 0 && j.names.every((_, r) => typeof j.finals[r] === 'number');
export const homeroom = () => Object.values(classes()).find((c) => teachers().some((t) => t.id === c.teacher && t.id === account()?.teacherId));
export function todayLessons() {
  const t = teachers().find((x) => x.id === account()?.teacherId);
  if (!t || holidayOn(NOW)) return [];
  const day = weekday(NOW);
  return Object.values(classes()).flatMap((c) => scheduleOf(c.key)[day].flatMap((s, n) => {
    const explicit = lessonTeacher(c.key, day, n);
    const mine = explicit ? explicit === t.id : t.load.some(([subj, cs]) => subj === s && cs.includes(c.key));
    return s && mine ? [{ time: lessonTimes(day)[n], start: bells(day)[n].start, end: bells(day)[n].end, cls: c.name, subj: SUBJECTS[s].name, key: c.key + '-' + s, room: SUBJECTS[s].room }] : [];
  })).sort((a, b) => a.start.localeCompare(b.start));
}

function allJournalKeys() {
  if (serverSession) {
    return server.session.journalKeys.map(({ key, cls, subj, subjectKey }) => ({ key, cls, subj, subjectKey }));
  }
  const keys = Object.keys(JOURNALS).map((key) => ({ key, cls: JOURNALS[key].cls, subj: JOURNALS[key].subj, subjectKey: key.slice(key.indexOf('-') + 1) }));
  Object.entries(state.journals || {}).forEach(([key, j]) => { if (!keys.some((k) => k.key === key)) keys.push({ key, cls: j.cls, subj: j.subj, subjectKey: j.subjectKey }); });
  if (role() === 'teacher') teachers().filter((t) => t.id === account()?.teacherId).forEach((t) => t.load.forEach(([s, cs]) => cs.forEach((c) => {
    const key = c + '-' + s;
    if (!keys.some((k) => k.key === key) && classes()[c]) keys.push({ key, cls: classes()[c].name, subj: SUBJECTS[s].name, subjectKey: s });
  })));
  return keys;
}
export function studentById(id) {
  if (serverSession) return STUDENTS[id] || Object.values(STUDENTS).find((s) => s.id === String(id)) || null;
  const seed = STUDENTS[id];
  for (const c of Object.values(classes())) {
    const s = c.students.find((x) => seed ? x.login === loginOf(seed.name) : x.login === id);
    if (s) return { id: seed ? id : s.login, name: s.name, first: s.name.split(' ')[1] || s.name, initials: s.name.split(' ').slice(0, 2).reverse().map((w) => w[0]).join(''), cls: c.name };
  }
  return null;
}
export function accountByLogin(key) {
  key = accountKey(key);
  if (state.retiredLogins?.includes(key)) return null;
  const base = state.accounts?.[key] || ACCOUNTS[key];
  if (base?.role === 'parent') {
    const children = (base.children || []).filter((id) => studentById(id));
    return children.length ? { ...base, children } : null;
  }
  if (base && base.role !== 'student' && base.role !== 'teacher') return base;
  for (const c of Object.values(classes())) {
    const s = c.students.find((x) => x.login === key);
    if (s) { const id = base?.studentId || key, st = studentById(id); return { ...st, studentId: id, role: 'student' }; }
  }
  const t = teachers().find((x) => teacherLogin(x) === key);
  if (t) return { ...base, teacherId: t.id, name: t.name, first: t.name.split(' ').slice(1).join(' '), initials: t.name.split(' ').slice(0, 2).map((w) => w[0]).reverse().join(''), role: 'teacher' };
  return state.accounts?.[key] || null;
}
export function createParentAccess(name, sid) {
  const result = createParentsAccess([name], sid);
  return result.error ? result : { ...result.parents[0], error: '' };
}
function parentClass(sid) {
  return Object.values(classes()).find((c) => c.students.some((s) => studentIdForLogin(s.login) === sid));
}
function parentAccessError(sid) {
  const cls = parentClass(sid);
  if (!cls) return 'Ученик больше не найден. Обновите список класса.';
  return role() === 'admin' || role() === 'teacher' && homeroom()?.key === cls.key ? '' : 'Менять родителей может администратор или классный руководитель этого класса.';
}
export function parentsForStudent(s) {
  const sid = studentIdForLogin(s.login);
  return Object.keys({ ...ACCOUNTS, ...state.accounts }).filter((key) => {
    const a = accountByLogin(key);
    return a?.role === 'parent' && a.children.includes(sid);
  });
}
export function createParentsAccess(values, sid) {
  if (refresh(true)) return { error: SAVE_ERROR };
  const error = parentAccessError(sid);
  if (error) return { error };
  const names = Array.isArray(values) ? values.map((name) => String(name).trim().replace(/\s+/g, ' ')) : [];
  if (!names.length || names.some((name) => name.length > 120 || name.split(' ').length < 2 || !/[а-яёa-z]/i.test(name))) return { error: 'Для каждого родителя введите фамилию и имя, не больше 120 символов.' };
  const accounts = { ...state.accounts }, invitations = { ...state.invitations }, all = classes(), parents = [];
  const taken = new Set(Object.values(invitations).map(inviteCode));
  for (const name of names) {
    let key = uniqueLogin(name), i = 2;
    while (accounts[key] || loginTaken(key)) key = loginOf(name) + i++;
    accounts[key] = { role: 'parent', name, first: name.split(' ').slice(1).join(' '), initials: name.split(' ').slice(0, 2).map((w) => w[0]).reverse().join(''), children: [sid] };
    let code;
    do { code = 'VK-' + randomCode() + '-' + randomCode(); } while (taken.has(code));
    taken.add(code);
    const item = { invite_code: code, used: false, schoolId: SCHOOL_ID };
    invitations[key] = item;
    parents.push({ login: key, invite_code: code, invitation: invitationResult(item) });
  }
  const pupil = all[parentClass(sid).key].students.find((s) => studentIdForLogin(s.login) === sid);
  pupil.parentLogins = [...parentsForStudent(pupil), ...parents.map((p) => p.login)];
  pupil.parentLogin = parents[0].login;
  pupil.parent = names[0];
  return set({ accounts, invitations, classes: all }) ? { parents, error: '' } : { error: SAVE_ERROR };
}
export function removeParentAccess(sid, value) {
  if (refresh(true)) return SAVE_ERROR;
  const error = parentAccessError(sid);
  if (error) return error;
  const key = accountKey(value), parent = accountByLogin(key);
  if (parent?.role !== 'parent' || !parent.children.includes(sid)) return 'Родитель уже удалён из списка ребёнка.';
  const children = parent.children.filter((id) => id !== sid), all = classes();
  const pupil = all[parentClass(sid).key].students.find((s) => studentIdForLogin(s.login) === sid);
  pupil.parentLogins = parentsForStudent(pupil).filter((login) => login !== key);
  pupil.parentLogin = pupil.parentLogins[0] || '';
  pupil.parent = pupil.parentLogin ? accountByLogin(pupil.parentLogin).name : '';
  const patch = { classes: all, accounts: { ...state.accounts, [key]: { ...parent, children } } };
  if (!children.length) {
    patch.retiredLogins = [...new Set([...(state.retiredLogins || []), key, loginName(key)])];
    for (const field of ['accounts', 'invitations', 'passwords', 'maxDone', 'avatars', 'loginNames']) {
      patch[field] = { ...(patch[field] || state[field]) }; delete patch[field][key];
    }
    for (const [inviteKey, invite] of Object.entries(patch.invitations)) if (invite.accountKey === key) delete patch.invitations[inviteKey];
    patch.maxProfiles = Object.fromEntries(Object.entries(state.maxProfiles || {}).filter(([, login]) => login !== key));
  }
  return set(patch) ? '' : SAVE_ERROR;
}
export function parentForStudent(s) {
  const sid = studentIdForLogin(s.login);
  const valid = (key) => accountByLogin(key)?.role === 'parent' && accountByLogin(key).children.includes(sid);
  if (s.parentLogin) return valid(s.parentLogin) ? s.parentLogin : '';
  if (!s.parent || s.parent === 'выдан') return '';
  const matches = Object.entries({ ...ACCOUNTS, ...state.accounts }).filter(([key, a]) => a.name === s.parent && valid(key));
  return matches.length === 1 ? matches[0][0] : '';
}
export const studentIdForLogin = (login) => Object.values(STUDENTS).find((child) => loginOf(child.name) === login)?.id || login;
export const uniqueLogin = (name) => { let key = loginOf(name), i = 2; while (loginTaken(key)) key = loginOf(name) + i++; return key; };
export const teacherLogin = (t) => t.login || Object.keys(ACCOUNTS).find((k) => ACCOUNTS[k].role === 'teacher' && ACCOUNTS[k].name === t.name) || loginOf(t.name);

const initialSchool = school();
configureGrading(initialSchool.scale, boundsFor(initialSchool, initialSchool.scale));
if (state.session && !accountByLogin(state.session.login)) state.session = null;

export { NOW, SCHOOL, SUBJECTS, STUDENTS, parse, weekday };
