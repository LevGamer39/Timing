// Запросы идут на тот же домен, что и сама страница: всё проксирует nginx.
// Жёсткий порт ломал бы прод за обратным прокси, а на https ещё и блокировался
// браузером как смешанное содержимое.
const API = window.location.origin;

function getToken() { return localStorage.getItem('token'); }
function getUser()  { return JSON.parse(localStorage.getItem('user') || 'null'); }
function setAuth(token, user) {
  localStorage.setItem('token', token);
  localStorage.setItem('user', JSON.stringify(user));
}
// Страница логина отдаётся по /login, а не /login.html: имя файла во
// frontend/ - login.html, а маршрут в main.py именно такой.
const LOGIN_URL = '/login';

function logout() {
  localStorage.removeItem('token');
  localStorage.removeItem('user');
  window.location.href = LOGIN_URL;
}

function requireAuth(allowedRoles) {
  const token = getToken();
  const user = getUser();
  if (!token || !user) { window.location.href = LOGIN_URL; return false; }
  if (allowedRoles && !allowedRoles.includes(user.role)) {
    window.location.href = LOGIN_URL;
    return false;
  }
  return user;
}

async function request(path, options = {}) {
  const token = getToken();
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(API + path, { ...options, headers });
  if (res.status === 401) { logout(); return; }
  const text = await res.text();
  let data;
  try { data = JSON.parse(text); } catch { data = { detail: text }; }
  if (!res.ok) throw new Error(data.detail || 'Ошибка запроса');
  return data;
}

async function get(path)         { return request(path, { method: 'GET' }); }
async function post(path, body)  { return request(path, { method: 'POST',  body: JSON.stringify(body) }); }
async function patch(path, body) { return request(path, { method: 'PATCH', body: JSON.stringify(body) }); }
async function del(path)         { return request(path, { method: 'DELETE' }); }

async function postForm(path, body) {
  const token = getToken();
  const headers = { 'Content-Type': 'application/x-www-form-urlencoded' };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(API + path, { method: 'POST', headers, body: new URLSearchParams(body).toString() });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Ошибка');
  return data;
}

function showAlert(id, message, type = 'error') {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = message;
  el.className = `alert alert-${type} show`;
}

function fullName(user) {
  if (!user) return '';
  return `${user.first_name || ''} ${user.last_name || ''}`.trim();
}

function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

function formatDateTime(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function priorityBadge(p) {
  const map = { low: ['gray','Низкий'], medium: ['yellow','Средний'], high: ['red','Высокий'], critical: ['red','Критичный'] };
  const [cls, label] = map[p] || ['gray', p];
  return `<span class="badge badge-${cls}">${label}</span>`;
}

function statusBadge(s) {
  const map = { pending: ['gray','Ожидает'], in_progress: ['yellow','В процессе'], done: ['green','Выполнено'], overdue: ['red','Просрочено'] };
  const [cls, label] = map[s] || ['gray', s];
  return `<span class="badge badge-${cls}">${label}</span>`;
}
