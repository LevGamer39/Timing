// Сетевой слой SPA. Единственное место, где живёт fetch к /api.
// Демо-режим (без токена) работает на данных из store.js/demo-*.js.

const TOKEN_KEY = 'timing-token';

// При локальной разработке страницу отдаёт http.server на 5173, а API —
// uvicorn на 8000. В проде всё приходит с одного origin, поэтому база пустая.
const API_BASE = (() => {
  if (globalThis.__TIMING_API__ !== undefined) return String(globalThis.__TIMING_API__).replace(/\/$/, '');
  if (typeof location === 'undefined') return '';
  const local = ['127.0.0.1', 'localhost'].includes(location.hostname);
  if (local && location.port && location.port !== '8000' && location.port !== '80' && location.port !== '443') return 'http://127.0.0.1:8000';
  return '';
})();

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export const token = () => {
  try { return localStorage.getItem(TOKEN_KEY) || ''; } catch { return ''; }
};

export const setToken = (value) => {
  try { value ? localStorage.setItem(TOKEN_KEY, value) : localStorage.removeItem(TOKEN_KEY); } catch { /* приватный режим */ }
};

export const hasToken = () => Boolean(token());

// Пояс устройства: «завтра 18:00» должно означать местное время, а не UTC.
// Если браузер его не отдал, бэкенд возьмёт TIMEZONE из .env.
export const timezone = () => {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch { return ''; }
};

async function request(path, { method = 'GET', body, form, auth = true } = {}) {
  const headers = {};
  if (auth && token()) headers.Authorization = 'Bearer ' + token();
  let payload;
  if (form) {
    headers['Content-Type'] = 'application/x-www-form-urlencoded';
    payload = new URLSearchParams(form).toString();
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(API_BASE + path, { method, headers, body: payload });
  } catch {
    throw new ApiError('Сервер недоступен. Проверьте подключение.', 0);
  }

  if (response.status === 204) return null;
  const type = response.headers.get('content-type') || '';
  const data = type.includes('application/json') ? await response.json().catch(() => null) : await response.text();
  if (!response.ok) {
    if (response.status === 401 && auth) setToken('');
    const detail = data && typeof data === 'object' ? data.detail : data;
    throw new ApiError(typeof detail === 'string' && detail ? detail : 'Ошибка запроса (' + response.status + ')', response.status);
  }
  return data;
}

// --- Вход ---------------------------------------------------------------

export const loginPassword = async (login, password) => {
  const data = await request('/api/users/login', { method: 'POST', form: { username: login, password }, auth: false });
  if (data?.access_token) setToken(data.access_token);
  return data;
};

export const loginMaxCode = async (code) => {
  const data = await request('/api/users/auth/max-code', { method: 'POST', body: { code }, auth: false });
  if (data?.access_token) setToken(data.access_token);
  return data;
};

export const register = (body) => request('/api/users/register', { method: 'POST', body, auth: false });
export const verifyEmail = (code) => request('/api/users/verify-email', { method: 'POST', body: { code }, auth: false });
export const resendVerification = (email) => request('/api/users/resend-verification?email=' + encodeURIComponent(email), { method: 'POST', auth: false });
export const requestPasswordReset = (email) => request('/api/users/password-reset/request', { method: 'POST', body: { email }, auth: false });
export const confirmPasswordReset = (token, new_password) => request('/api/users/password-reset/confirm', { method: 'POST', body: { token, new_password }, auth: false });
export const requestEmailChange = (email) => request('/api/users/me/change-email', { method: 'POST', body: { email } });
export const confirmEmailChange = (token) => request('/api/users/me/change-email/confirm?token=' + encodeURIComponent(token), { method: 'POST', auth: false });
export const joinClass = (code) => request('/api/classes/join?invite_code=' + encodeURIComponent(code), { method: 'POST' });

export const yandexLoginUrl = () => request('/api/auth/yandex/login-url', { auth: false });

export const me = () => request('/api/users/me');
export const patchMe = (body) => request('/api/users/me', { method: 'PATCH', body });
export const changePassword = (current_password, new_password) => request('/api/users/me/change-password', { method: 'POST', body: { current_password, new_password } });
export const children = () => request('/api/users/me/children');
export const parentLinkToken = () => request('/api/users/me/parent-link-token', { method: 'POST' });
export const linkParent = (code) => request('/api/users/me/link-parent?code=' + encodeURIComponent(code), { method: 'POST' });
export const notifications = () => request('/api/notifications');
export const readNotification = (id) => request('/api/notifications/' + id + '/read', { method: 'POST' });
export const readAllNotifications = () => request('/api/notifications/read-all', { method: 'POST' });

// --- Ученик / родитель ----------------------------------------------------

export const myGrades = () => request('/api/grades/my');
export const myAverages = () => request('/api/grades/my/averages');
export const mySummary = () => request('/api/grades/my/summary');
export const childGrades = (id) => request('/api/grades/child/' + id);
export const myTasks = () => request('/api/tasks');
export const childTasks = (id) => request('/api/tasks/child/' + id);
export const mySchedule = () => request('/api/schedule/my');
export const childSchedule = (id) => request('/api/schedule/child/' + id);
export const createTask = (body) => request('/api/tasks', { method: 'POST', body });
export const updateTask = (id, body) => request('/api/tasks/' + id, { method: 'PATCH', body });
export const completeTask = (id) => request('/api/tasks/' + id + '/complete', { method: 'POST' });
export const deleteTask = (id) => request('/api/tasks/' + id, { method: 'DELETE' });
export const taskSubtasks = (id) => request('/api/tasks/' + id + '/subtasks');
export const aiSubtasks = (id) => request('/api/ai/subtasks/' + id, { method: 'POST' });
export const aiAnalyzeLoad = () => request('/api/ai/analyze-load', { method: 'POST' });

// --- Учитель --------------------------------------------------------------

export const myAssignments = () => request('/api/assignments');
export const createAssignment = (body) => request('/api/assignments', { method: 'POST', body });
export const assignmentProgress = (id) => request('/api/assignments/' + id + '/progress');
export const acceptAssignment = (id, body) => request('/api/assignments/' + id + '/accept', { method: 'POST', body: body || {} });
export const classWorkload = (classId, date) => request('/api/assignments/workload/' + classId + '?date=' + encodeURIComponent(date));
export const assignmentGrades = (id) => request('/api/grades/assignment/' + id);
export const deleteAssignment = (id) => request('/api/assignments/' + id, { method: 'DELETE' });
export const myClasses = () => request('/api/classes');
export const classDashboard = (id, params) => {
  const qs = params ? '?' + new URLSearchParams(params).toString() : '';
  return request('/api/classes/' + id + '/overview' + qs);
};
export const teacherDashboard = () => request('/api/classes/dashboard');
export const classOverview = (id) => request('/api/classes/' + id + '/overview');
export const gradeStudent = (body) => request('/api/grades', { method: 'POST', body });
export const gradeBulk = (body) => request('/api/grades/bulk', { method: 'POST', body });

// --- Журнал ---------------------------------------------------------------

export const journalList = () => request('/api/journal');
export const journalGet = (classId, subject) => request('/api/journal/' + classId + '/' + encodeURIComponent(subject));
export const journalSave = (classId, subject, body) => request('/api/journal/' + classId + '/' + encodeURIComponent(subject), { method: 'PUT', body });
export const journalWorkTypes = () => request('/api/journal/work-types');
export const createWorkType = (body) => request('/api/journal/work-types', { method: 'POST', body });
export const updateWorkType = (code, body) => request('/api/journal/work-types/' + encodeURIComponent(code), { method: 'PUT', body });
export const deleteWorkType = (code) => request('/api/journal/work-types/' + encodeURIComponent(code), { method: 'DELETE' });

// --- Посещаемость ---------------------------------------------------------

export const classAttendance = (classId, from, to) => {
  const qs = new URLSearchParams({ from, to }).toString();
  return request('/api/attendance/class/' + classId + '?' + qs);
};
export const saveAttendance = (classId, date, marks) => request('/api/attendance/class/' + classId + '/' + date, { method: 'PUT', body: { marks } });

// --- Почта ----------------------------------------------------------------

export const mailbox = () => request('/api/messages');
export const messagePeople = () => request('/api/messages/people');
export const sendMessage = (body) => request('/api/messages', { method: 'POST', body });
export const readMessage = (id) => request('/api/messages/' + id + '/read', { method: 'POST' });
export const deleteMessage = (id) => request('/api/messages/' + id, { method: 'DELETE' });

export const uploadAttachment = async (file) => {
  const headers = {};
  if (token()) headers.Authorization = 'Bearer ' + token();
  const form = new FormData();
  form.append('file', file);
  let response;
  try {
    response = await fetch(API_BASE + '/api/messages/attachments', { method: 'POST', headers, body: form });
  } catch {
    throw new ApiError('Сервер недоступен. Проверьте подключение.', 0);
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError((data && data.detail) || 'Не удалось загрузить файл', response.status);
  return data;
};

export const deleteAttachment = (fileToken) => request('/api/messages/attachments/' + encodeURIComponent(fileToken), { method: 'DELETE' });

export const downloadAttachment = async (fileToken) => {
  const headers = {};
  if (token()) headers.Authorization = 'Bearer ' + token();
  const response = await fetch(API_BASE + '/api/messages/attachments/' + encodeURIComponent(fileToken), { headers });
  if (!response.ok) throw new ApiError('Файл недоступен', response.status);
  return response.blob();
};

// --- Расписание класса ----------------------------------------------------

export const classSchedule = (id) => request('/api/schedule/class/' + id);
export const saveClassLesson = (id, weekday, index, body) => request('/api/schedule/class/' + id + '/' + weekday + '/' + index, { method: 'PUT', body });
export const deleteClassLesson = (id, weekday, index) => request('/api/schedule/class/' + id + '/' + weekday + '/' + index, { method: 'DELETE' });

// --- Администратор --------------------------------------------------------

export const adminDashboard = () => request('/api/admin/dashboard');
export const adminClasses = () => request('/api/admin/classes');
export const adminTeachers = () => request('/api/admin/teachers');
export const adminStudents = () => request('/api/admin/students');
export const createClass = (body) => request('/api/admin/classes', { method: 'POST', body });
export const updateClass = (id, body) => request('/api/admin/classes/' + id, { method: 'PATCH', body });
export const deleteClass = (id) => request('/api/admin/classes/' + id, { method: 'DELETE' });
export const addStudentToClass = (id, body) => request('/api/admin/classes/' + id + '/students', { method: 'POST', body });
export const removeStudentFromClass = (id, studentId) => request('/api/admin/classes/' + id + '/students/' + studentId, { method: 'DELETE' });
export const inviteCodes = () => request('/api/admin/invite-codes');
export const createInviteCode = (body) => request('/api/admin/invite-codes', { method: 'POST', body });
export const deleteInviteCode = (id) => request('/api/admin/invite-codes/' + id, { method: 'DELETE' });
export const updateUser = (id, body) => request('/api/admin/users/' + id, { method: 'PATCH', body });

// Перенос учеников между установками: JSON по схеме /static/schemas/student-migration.schema.json
export const exportStudents = () => request('/api/admin/migration/students');
export const importStudents = (doc) => request('/api/admin/migration/students', { method: 'POST', body: doc });

// --- Настройки школы ------------------------------------------------------

export const schoolSettings = () => request('/api/school/settings');
export const saveSchoolSettings = (payload) => request('/api/school/settings', { method: 'PUT', body: { payload } });

// --- ИИ -------------------------------------------------------------------

export const aiPrompts = () => request('/api/ai/quick-prompts');
export const aiChats = () => request('/api/ai/chats');
export const aiDeleteChat = (id) => request('/api/ai/chats/' + id, { method: 'DELETE' });
export const aiHistory = (chatId) => request('/api/ai/history' + (chatId ? '?chat_id=' + chatId : ''));
export const aiChat = (message, options = {}) => request('/api/ai/chat', {
  method: 'POST',
  body: {
    message,
    chat_id: options.chatId ?? null,
    new_chat: Boolean(options.newChat),
    timezone: timezone(),
  },
});
export const aiAction = (body) => request('/api/ai/actions', { method: 'POST', body: { ...body, timezone: timezone() } });
export const aiClearHistory = () => request('/api/ai/history', { method: 'DELETE' });

// --- Статистика ------------------------------------------------------------
// Ученик ходит в /my, родитель — в /child/{id}: одни и те же данные.

const statsPath = (kind, childId) => '/api/stats/' + kind + (childId ? '/child/' + childId : '/my');

export const statsDaily = (childId) => request(statsPath('daily', childId));
export const statsWeekly = (childId) => request(statsPath('weekly', childId));
export const statsDynamics = (childId, weeks) => request(statsPath('dynamics', childId) + (weeks ? '?weeks=' + weeks : ''));
export const statsInsight = (childId) => request(statsPath('insight', childId));
