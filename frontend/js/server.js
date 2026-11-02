// Переходный слой между API и демо-структурами SPA.
//
// Экраны написаны под синхронные функции store.js и форму данных из data.js.
// Чтобы не переписывать все экраны сразу, ответы API раскладываются в те же
// объекты (STUDENTS, GRADES, HOMEWORK, CLASSES, SCHEDULES, JOURNALS).
// Демо-данные остаются запасным вариантом: без токена приложение работает
// как раньше.

import * as api from './api.js';
import { STUDENTS, GRADES, HOMEWORK, SUBJECTS } from './data.js';
import { CLASSES, TEACHERS, SCHEDULES } from './admin-data.js';

const DEMO = {
  students: JSON.parse(JSON.stringify(STUDENTS)),
  grades: JSON.parse(JSON.stringify(GRADES)),
  homework: JSON.parse(JSON.stringify(HOMEWORK)),
  subjects: JSON.parse(JSON.stringify(SUBJECTS)),
  classes: JSON.parse(JSON.stringify(CLASSES)),
  teachers: JSON.parse(JSON.stringify(TEACHERS)),
  schedules: JSON.parse(JSON.stringify(SCHEDULES))
};

export const session = {
  profile: null,
  children: [],
  tasks: new Map(),
  times: [],
  timesByChild: new Map(),
  people: [],
  mail: { inbox: [], sent: [], unread: 0 },
  journals: {},
  journalKeys: [],
  journalStudents: {},
  workTypes: [],
  attendance: {},
  school: {},
  classKeys: {},
  schedTeachers: {},
  teacherBySubject: {},
  students: [],
  active: false
};

const PREFIX = { admin: 'a', teacher: 't', student: 's', parent: 'p' };
const ROLE_LABEL = {
  admin: 'Администрация',
  teacher: 'Учитель',
  student: 'Ученик',
  parent: 'Родитель'
};

let notifier = () => {};
export const setNotifier = (fn) => { notifier = fn || (() => {}); };
const notify = () => { try { notifier(); } catch { /* рендер не должен ломать данные */ } };

const TR = { а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ё: 'e', ж: 'zh', з: 'z', и: 'i', й: 'y', к: 'k', л: 'l', м: 'm', н: 'n', о: 'o', п: 'p', р: 'r', с: 's', т: 't', у: 'u', ф: 'f', х: 'kh', ц: 'ts', ч: 'ch', ш: 'sh', щ: 'shch', ъ: '', ы: 'y', ь: '', э: 'e', ю: 'yu', я: 'ya' };

export const slug = (value) => String(value || '')
  .toLowerCase()
  .split('')
  .map((c) => TR[c] ?? (/[a-z0-9]/.test(c) ? c : ''))
  .join('')
  .replace(/\s+/g, '-')
  .slice(0, 40) || 'subject';

const subjectKey = (name, room, teacher) => {
  const key = slug(name);
  const current = SUBJECTS[key];
  if (!current || (!current.room && room) || (!current.teacher && teacher)) {
    SUBJECTS[key] = {
      name: name || current?.name || key,
      room: room || current?.room || '',
      teacher: teacher || current?.teacher || ''
    };
  }
  return key;
};

const initials = (first, last) => ((first || ' ')[0] + (last || ' ')[0]).toUpperCase();
const fullName = (first, last) => [last, first].filter(Boolean).join(' ').trim();
const dateOf = (value) => (typeof value === 'string' ? value.slice(0, 10) : '');
const score = (value) => (typeof value === 'number' ? Math.max(0, Math.min(100, value * 20)) : value);
const personId = (person) => PREFIX[person.role] + '-' + person.id;

const isoToday = () => new Date().toISOString().slice(0, 10);
const isoDaysAgo = (days) => {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return date.toISOString().slice(0, 10);
};

export const classKey = (name) => session.classKeys[name] || name;

function studentRecord(profile, cls = '') {
  const name = fullName(profile.first_name, profile.last_name) || profile.login;
  return {
    id: String(profile.id),
    name,
    first: profile.first_name || '',
    last: profile.last_name || '',
    initials: initials(profile.first_name, profile.last_name),
    cls,
    schedule: [],
    login: profile.login,
    email: profile.email || ''
  };
}

function putGrades(student, list) {
  GRADES[student.id] = (list || []).map((g, i) => [
    dateOf(g.graded_at),
    subjectKey(g.subject),
    score(g.value),
    typeof g.weight === 'number' ? g.weight : 1,
    g.comment || 'Оценка',
    g.comment || '',
    'srv-' + (g.id ?? i)
  ]).filter((row) => row[0]).sort((a, b) => a[0].localeCompare(b[0]));
}

function putHomework(student, tasks) {
  HOMEWORK[student.id] = (tasks || [])
    .filter((t) => t.status !== 'done')
    .map((t) => {
      const date = dateOf(t.deadline);
      const subject = subjectKey(t.subject);
      if (date) session.tasks.set(student.id + '|' + date + '|' + subject, t.id);
      return [date, subject, t.title];
    })
    .filter((row) => row[0]);
}

export const taskId = (key) => session.tasks.get(key) || null;

function putSchedule(student, list) {
  const rows = [[], [], [], [], [], [], []];
  session.times = [[], [], [], [], [], [], []];
  (list || []).slice().sort((a, b) => String(a.start_time).localeCompare(String(b.start_time))).forEach((entry) => {
    const key = subjectKey(entry.subject, entry.room, entry.teacher_name);
    const day = Number(entry.weekday);
    if (!rows[day]) return;
    rows[day].push(key);
    session.times[day].push({ start: String(entry.start_time).slice(0, 5), end: String(entry.end_time).slice(0, 5) });
  });
  student.schedule = rows;
  SCHEDULES[student.cls] = rows;
  session.timesByChild.set(student.id, session.times);
}

export const activate = (id) => {
  session.times = session.timesByChild.get(String(id)) || [];
};

async function loadStudent(profile) {
  const cls = profile.class_name || profile.grade || '';
  const student = studentRecord(profile, cls);
  session.classKeys[cls] = cls;
  STUDENTS[student.id] = student;
  const [grades, tasks, schedule] = await Promise.all([
    api.myGrades().catch(() => []),
    api.myTasks().catch(() => []),
    api.mySchedule().catch(() => [])
  ]);
  putGrades(student, grades);
  putHomework(student, tasks);
  putSchedule(student, schedule);
  return student;
}

async function loadChild(profile) {
  const cls = profile.class_name || profile.grade || '';
  const student = studentRecord(profile, cls);
  session.classKeys[cls] = cls;
  STUDENTS[student.id] = student;
  const [grades, tasks, schedule] = await Promise.all([
    api.childGrades(profile.id).catch(() => []),
    api.childTasks(profile.id).catch(() => []),
    api.childSchedule(profile.id).catch(() => [])
  ]);
  putGrades(student, grades);
  putHomework(student, tasks);
  putSchedule(student, schedule);
  return student;
}

export function accountFor(profile, children) {
  const name = fullName(profile.first_name, profile.last_name) || profile.login;
  const account = {
    role: profile.role,
    login: profile.login,
    name,
    first: profile.first_name || '',
    initials: initials(profile.first_name, profile.last_name),
    title: profile.role === 'teacher' ? 'Учитель' : profile.role === 'admin' ? 'Администратор школы' : '',
    last: profile.last_name || '',
    subject: profile.teacher_subject || '',
    email: profile.email || '',
    notifyEmail: profile.notify_email !== false,
    notifyBrowser: profile.notify_browser !== false,
    digestTime: profile.notify_digest_time || '19:00'
  };
  if (profile.role === 'student') account.studentId = String(profile.id);
  if (profile.role === 'parent') account.children = children.map((child) => String(child.id));
  if (profile.role === 'teacher') account.teacherId = String(profile.id);
  return account;
}

export async function load() {
  const profile = await api.me();
  session.profile = profile;
  session.classKeys = {};
  session.journals = {};
  session.journalKeys = [];
  session.journalStudents = {};
  session.workTypes = [];
  session.attendance = {};
  session.schedTeachers = {};
  session.teacherBySubject = {};

  let children = [];
  if (profile.role === 'parent') children = await api.children().catch(() => []);
  const kids = [];
  if (profile.role === 'student') kids.push(await loadStudent(profile));
  if (profile.role === 'parent') for (const child of children) kids.push(await loadChild(child));

  session.children = kids;
  session.active = true;
  if (profile.role === 'teacher' || profile.role === 'admin') {
    const classes = await loadStaff(profile);
    await loadClassSchedules(classes, profile);
    if (profile.role === 'teacher') await loadJournals();
    await loadAttendance(classes);
  }
  await loadMail();
  const settings = await api.schoolSettings().catch(() => null);
  session.school = settings?.payload && typeof settings.payload === 'object' ? settings.payload : {};
  return { profile, children: kids };
}

async function loadStaff(profile) {
  // Классы и учителя нужны на экранах журнала, «моего класса» и админки.
  const classes = profile.role === 'admin' ? await api.adminClasses().catch(() => []) : await api.myClasses().catch(() => []);
  const teachers = profile.role === 'admin' ? await api.adminTeachers().catch(() => []) : [];
  if (Array.isArray(classes) && classes.length) {
    Object.keys(CLASSES).forEach((key) => delete CLASSES[key]);
    classes.forEach((c) => {
      const key = String(c.id);
      CLASSES[key] = {
        key,
        name: c.name,
        teacher: profile.role === 'teacher' ? String(profile.id) : String(c.teacher_id || ''),
        invite: c.invite_code || '',
        students: []
      };
      session.classKeys[c.name] = key;
    });
  }
  if (Array.isArray(teachers) && teachers.length) {
    TEACHERS.length = 0;
    teachers.forEach((t) => TEACHERS.push({
      id: String(t.id),
      name: [t.last_name, t.first_name].filter(Boolean).join(' ') || t.email || 'Учитель',
      subject: t.teacher_subject || '',
      classCount: Number(t.class_count || 0),
      load: []
    }));
  }
  if (profile.role === 'teacher') {
    TEACHERS.length = 0;
    TEACHERS.push({
      id: String(profile.id),
      name: fullName(profile.first_name, profile.last_name) || profile.login,
      load: []
    });
  }
  if (profile.role === 'teacher') {
    for (const c of classes) {
      const detail = await api.classOverview(c.id).catch(() => null);
      const item = CLASSES[String(c.id)];
      if (item && detail?.students) {
        item.students = detail.students.map((s) => ({
          id: String(s.id), name: s.name, login: String(s.id), status: 'ok', parent: ''
        }));
      }
    }
  }
  if (profile.role === 'admin') {
    const students = await api.adminStudents().catch(() => []);
    session.students = Array.isArray(students) ? students : [];
    (students || []).forEach((s) => {
      const item = CLASSES[String(s.class_id)];
      if (!item) return;
      item.students.push({
        id: String(s.id),
        name: [s.last_name, s.first_name].filter(Boolean).join(' ') || s.email || 'Ученик',
        login: String(s.id),
        status: s.is_active ? 'ok' : 'wait',
        parent: ''
      });
    });
  }
  return classes;
}

async function loadClassSchedules(classes, profile) {
  if (!Array.isArray(classes)) return;
  let times = null;
  for (const c of classes) {
    const key = String(c.id);
    const rows = await api.classSchedule(c.id).catch(() => []);
    const plan = [[], [], [], [], [], [], []];
    const slots = {};
    (rows || []).slice().sort((a, b) => Number(a.weekday) - Number(b.weekday) || Number(a.lesson_index) - Number(b.lesson_index)).forEach((row) => {
      const day = Number(row.weekday);
      if (!plan[day]) return;
      const index = Number(row.lesson_index);
      const subject = subjectKey(row.subject, row.room, row.teacher_name);
      plan[day][index] = subject;
      const known = TEACHERS.find((t) => t.name === row.teacher_name);
      const teacher = row.teacher_id ? String(row.teacher_id) : (known ? known.id : '');
      if (teacher) slots[day + '|' + index] = teacher;
      if (row.teacher_name) session.teacherBySubject[key + '|' + subject] = row.teacher_name;
      if (!times) times = [[], [], [], [], [], [], []];
      if (times[day] && !times[day][index]) {
        times[day][index] = {
          start: String(row.start_time).slice(0, 5),
          end: String(row.end_time).slice(0, 5)
        };
      }
    });
    SCHEDULES[key] = plan;
    session.schedTeachers[key] = slots;
  }
  if (times) session.times = times;
  if (profile.role === 'teacher' && TEACHERS.length) {
    // Учитель видит себя в колонке «учитель», даже если в классе он один.
    TEACHERS[0].load = [];
  }
}

async function loadJournals() {
  const list = await api.journalList().catch(() => []);
  if (!Array.isArray(list) || !list.length) return;
  const types = new Map();
  for (const meta of list) {
    const data = await api.journalGet(meta.class_id, meta.subject).catch(() => null);
    if (!data) continue;
    (data.work_types || []).forEach((t) => types.set(t.code, t));
    const key = String(meta.class_id) + '-' + subjectKey(data.subject);
    const roster = (data.students || []).map((s) => String(s.id));
    const names = (data.students || []).map((s) => s.name);
    const grades = {};
    const comments = {};
    (data.marks || []).forEach((mark) => {
      const r = roster.indexOf(String(mark.student_id));
      if (r < 0) return;
      const date = dateOf(mark.date);
      grades[r + '|' + date] = score(mark.value);
      if (mark.comment) comments[r + '|' + date] = mark.comment;
    });
    (data.absences || []).forEach((absence) => {
      const r = roster.indexOf(String(absence.student_id));
      if (r >= 0) grades[r + '|' + dateOf(absence.date)] = 'Н';
    });
    const finalsByPeriod = {};
    (data.finals || []).forEach((final) => {
      const r = roster.indexOf(String(final.student_id));
      if (r < 0) return;
      if (!finalsByPeriod[final.period]) finalsByPeriod[final.period] = {};
      finalsByPeriod[final.period][r] = score(final.value);
    });
    session.journals[key] = {
      key,
      cls: data.class_name,
      subj: data.subject,
      subjectKey: subjectKey(data.subject),
      names,
      lessons: (data.lessons || []).map((lesson) => ({
        date: dateOf(lesson.date),
        type: lesson.type,
        hw: lesson.homework || ''
      })),
      grades,
      comments,
      finalsByPeriod
    };
    session.journalKeys.push({
      key,
      cls: data.class_name,
      subj: data.subject,
      subjectKey: session.journals[key].subjectKey,
      classId: meta.class_id,
      subject: meta.subject
    });
    session.journalStudents[key] = roster;
    const classKeyValue = String(meta.class_id);
    if (CLASSES[classKeyValue]) {
      CLASSES[classKeyValue].students = (data.students || []).map((s) => ({
        id: String(s.id), name: s.name, login: String(s.id), status: 'ok', parent: ''
      }));
    }
  }
  session.workTypes = [...types.values()].map((t) => ({ id: t.code, name: t.name, w: t.weight }));
}

async function loadAttendance(classes) {
  if (!Array.isArray(classes)) return;
  const from = isoDaysAgo(180);
  const to = isoToday();
  for (const c of classes) {
    const key = String(c.id);
    const roster = (CLASSES[key]?.students || []).map((s) => String(s.id));
    if (!roster.length) continue;
    const marks = await api.classAttendance(c.id, from, to).catch(() => []);
    (marks || []).forEach((mark) => {
      const r = roster.indexOf(String(mark.student_id));
      if (r < 0) return;
      const day = dateOf(mark.date);
      const slot = key + '|' + day;
      if (!session.attendance[slot]) session.attendance[slot] = {};
      session.attendance[slot][r + '|' + mark.lesson_index] = true;
    });
  }
}

async function loadMail() {
  const [people, box] = await Promise.all([
    api.messagePeople().catch(() => []),
    api.mailbox().catch(() => null)
  ]);
  session.people = (people || []).map((p) => ({
    id: personId(p),
    name: p.name,
    role: ROLE_LABEL[p.role] || p.role,
    rawRole: p.role
  }));
  const byId = new Map(session.people.map((p) => [p.id, p]));
  const raw = (list) => (list || []).map((m) => ({
    id: String(m.id),
    from: personId(m.sender),
    to: personId(m.recipient),
    subject: m.subject,
    body: m.body,
    date: m.created_at,
    unread: !m.is_read,
    files: m.files || [],
    senderName: m.sender.name,
    recipientName: m.recipient.name
  }));
  session.mail = box
    ? { inbox: raw(box.inbox), sent: raw(box.sent), unread: box.unread }
    : { inbox: [], sent: [], unread: 0 };
  session.peopleById = byId;
}

export const person = (id) => session.people.find((p) => p.id === id)
  || { id, name: id, role: '' };

// --- Журнал ---------------------------------------------------------------

const journalQueues = new Map();

function journalPayload(j) {
  const meta = session.journalKeys.find((k) => k.key === j.key);
  if (!meta) return null;
  const roster = session.journalStudents[j.key] || [];
  const lessons = (j.lessons || []).map((lesson) => ({
    date: lesson.date,
    type: lesson.type || 'lesson',
    homework: lesson.hw || ''
  }));
  const marks = [];
  const absences = [];
  Object.entries(j.grades || {}).forEach(([key, value]) => {
    const [r, date] = key.split('|');
    const student = roster[Number(r)];
    if (!student || !date) return;
    if (value === 'Н') absences.push({ student_id: Number(student), date });
    else if (typeof value === 'number') {
      marks.push({
        student_id: Number(student),
        date,
        value,
        comment: j.comments?.[key] || null
      });
    }
  });
  const finals = [];
  Object.entries(j.finalsByPeriod || {}).forEach(([period, map]) => {
    Object.entries(map || {}).forEach(([r, value]) => {
      const student = roster[Number(r)];
      if (student && typeof value === 'number') {
        finals.push({ student_id: Number(student), period, value });
      }
    });
  });
  return { meta, payload: { lessons, marks, absences, finals } };
}

export function saveJournal(j) {
  const data = journalPayload(j);
  if (!data) return false;
  const local = JSON.parse(JSON.stringify(j));
  delete local.periodKey;
  delete local.periodFrom;
  delete local.periodTo;
  session.journals[j.key] = local;
  const previous = journalQueues.get(j.key) || Promise.resolve();
  const next = previous.catch(() => {}).then(
    () => api.journalSave(data.meta.classId, data.meta.subject, data.payload)
  ).catch(() => { refreshJournals(); });
  journalQueues.set(j.key, next);
  return true;
}

export async function refreshJournals() {
  session.journals = {};
  session.journalKeys = [];
  session.journalStudents = {};
  await loadJournals();
  notify();
}

export function setWorkTypes(list) {
  const before = new Map(session.workTypes.map((t) => [t.id, t]));
  session.workTypes = (list || []).map((t) => ({ id: t.id, name: t.name, w: t.w }));
  const codes = new Set(session.workTypes.map((t) => t.id));
  session.workTypes.forEach((t) => {
    const old = before.get(t.id);
    if (old && old.name === t.name && old.w === t.w) return;
    if (old) api.updateWorkType(t.id, { name: t.name, weight: t.w }).catch(() => notify());
    else api.createWorkType({ code: t.id, name: t.name, weight: t.w }).catch(() => notify());
  });
  before.forEach((_old, code) => {
    if (!codes.has(code)) api.deleteWorkType(code).catch(() => notify());
  });
  return true;
}

export function removeWorkType(code) {
  session.workTypes = session.workTypes.filter((t) => t.id !== code);
  Object.values(session.journals).forEach((journal) => {
    (journal.lessons || []).forEach((lesson) => {
      if (lesson.type === code) lesson.type = 'lesson';
    });
  });
  api.deleteWorkType(code).catch(() => notify());
  return true;
}

// --- Посещаемость ---------------------------------------------------------

export function attendance(cls, date) {
  return Object.assign({}, session.attendance[cls + '|' + date]);
}

export function saveAttendance(cls, date, marks) {
  const classId = Number(cls);
  if (!classId) return false;
  session.attendance[cls + '|' + date] = JSON.parse(JSON.stringify(marks));
  const roster = (CLASSES[cls]?.students || []).map((s) => String(s.id));
  const list = Object.keys(marks || {}).flatMap((key) => {
    const [r, n] = key.split('|').map(Number);
    const student = roster[r];
    return student ? [{ student_id: Number(student), lesson_index: n }] : [];
  });
  api.saveAttendance(classId, date, list).catch(() => notify());
  return true;
}

// --- Почта ----------------------------------------------------------------

export function inbox() { return session.mail.inbox; }
export function sent() { return session.mail.sent; }

export function markRead(id) {
  const message = session.mail.inbox.find((m) => String(m.id) === String(id));
  if (!message) return true;
  if (message.unread) {
    message.unread = false;
    session.mail.unread = Math.max(0, session.mail.unread - 1);
    api.readMessage(message.id).catch(() => notify());
  }
  return true;
}

export function deleteMail(id) {
  session.mail.inbox = session.mail.inbox.filter((m) => String(m.id) !== String(id));
  session.mail.sent = session.mail.sent.filter((m) => String(m.id) !== String(id));
  api.deleteMessage(id).catch(() => notify());
  return true;
}

export function sendMail(message) {
  const recipient = session.people.find((p) => p.id === message.to);
  const senderId = PREFIX[session.profile.role] + '-' + session.profile.id;
  if (!recipient) return false;
  const temp = {
    id: 'my' + Date.now(),
    from: senderId,
    to: recipient.id,
    subject: message.subject,
    body: message.body,
    date: new Date().toISOString(),
    unread: false,
    files: message.files || []
  };
  session.mail.sent.unshift(temp);
  const tokens = (message.files || []).map((f) => (typeof f === 'string' ? f : f.token)).filter(Boolean);
  api.sendMessage({
    recipient_id: Number(recipient.id.split('-').slice(1).join('-')),
    subject: message.subject,
    body: message.body,
    files: tokens
  }).then(() => refreshMail()).catch(() => notify());
  return true;
}

export async function refreshMail() {
  await loadMail();
  notify();
}

// --- Расписание класса ----------------------------------------------------

export function saveLesson(cls, plan, day, index, teacherId, time, room) {
  const classId = Number(cls);
  if (!classId) return false;
  const subject = plan?.[day]?.[index] || '';
  SCHEDULES[cls] = plan;
  const slots = Object.assign({}, session.schedTeachers[cls] || {});
  if (subject) slots[day + '|' + index] = teacherId || '';
  else delete slots[day + '|' + index];
  session.schedTeachers[cls] = slots;
  if (!subject) {
    api.deleteClassLesson(classId, day, index).catch(() => notify());
    return true;
  }
  const teacher = teacherId ? TEACHERS.find((t) => t.id === teacherId) : null;
  const teacherName = teacher ? teacher.name : '';
  const key = subjectKey(subject, room);
  if (teacherName) session.teacherBySubject[cls + '|' + key] = teacherName;
  api.saveClassLesson(classId, day, index, {
    subject: SUBJECTS[subject]?.name || subject,
    weekday: day,
    teacher_id: teacher && /^\d+$/.test(teacherId) ? Number(teacherId) : null,
    teacher_name: teacherName || null,
    start_time: time?.start || '08:30',
    end_time: time?.end || '09:10',
    room: room || null
  }).catch(() => notify());
  return true;
}

export function removeLesson(cls, day, index) {
  const classId = Number(cls);
  const plan = SCHEDULES[cls] || [];
  if (plan[day]) plan[day][index] = null;
  const slots = Object.assign({}, session.schedTeachers[cls] || {});
  delete slots[day + '|' + index];
  session.schedTeachers[cls] = slots;
  if (classId) api.deleteClassLesson(classId, day, index).catch(() => notify());
  return true;
}

export function teacherFor(subject, cls) {
  return session.teacherBySubject[cls + '|' + subject] || '';
}

export function saveSchool(school) {
  session.school = JSON.parse(JSON.stringify(school || {}));
  api.saveSchoolSettings(session.school).catch(() => notify());
  return true;
}

// --- Профиль --------------------------------------------------------------

export function saveProfile(patch) {
  return api.patchMe(patch)
    .then((profile) => {
      session.profile = Object.assign({}, session.profile, profile);
      notify();
      return session.profile;
    })
    .catch(() => null);
}

export function changePassword(current, next) {
  return api.changePassword(current, next)
    .then(() => '')
    .catch((error) => (error && error.message) || 'Не удалось сменить пароль.');
}

// --- Задачи ---------------------------------------------------------------

export const taskList = () => api.myTasks();
export const taskCreate = (body) => api.createTask(body);
export const taskUpdate = (id, body) => api.updateTask(id, body);
export const taskDelete = (id) => api.deleteTask(id);
export const taskComplete = (id) => api.completeTask(id);
export const taskSubtasks = (id) => api.taskSubtasks(id);
export const taskAiSubtasks = (id) => api.aiSubtasks(id);

// --- Задания класса -------------------------------------------------------

export const assignmentList = () => api.myAssignments();
export const assignmentCreate = (body) => api.createAssignment(body);
export const assignmentDelete = (id) => api.deleteAssignment(Number(id));
export const assignmentProgress = (id) => api.assignmentProgress(Number(id));
export const assignmentGrades = (id) => api.assignmentGrades(Number(id));
export const assignmentGrade = (assignmentId, subject, items) => api.gradeBulk({ assignment_id: Number(assignmentId), subject, items });

// --- Классы и учителя -----------------------------------------------------

export async function refreshStaff() {
  if (!session.profile) return false;
  try {
    await loadStaff(session.profile);
    notify();
    return true;
  } catch {
    return false;
  }
}

const mutation = (run) => run
  .then(async (result) => {
    await refreshStaff();
    return result ?? true;
  })
  .catch((error) => (error && error.message) || 'Не удалось сохранить изменения.');

export const createClass = (name, teacherId) => mutation(api.createClass({ name, teacher_id: Number(teacherId) }));
export const patchClass = (key, patch) => mutation(api.updateClass(Number(key), patch));
export const deleteClass = (key) => mutation(api.deleteClass(Number(key)));
export const addStudentToClass = (key, studentId) => mutation(api.addStudentToClass(Number(key), { student_id: Number(studentId) }));
export const removeStudentFromClass = (key, studentId) => mutation(api.removeStudentFromClass(Number(key), Number(studentId)));
export const createTeacherInvite = () => mutation(api.createInviteCode());
export const inviteCodes = () => api.inviteCodes().catch(() => []);
export const deleteInviteCode = (id) => mutation(api.deleteInviteCode(Number(id)));
export const activateUser = (id, active = true) => mutation(api.updateUser(Number(id), { is_active: active }));
export const setTeacherSubject = (id, subject) => mutation(api.updateUser(Number(id), { teacher_subject: subject }));
export const deactivateTeacher = (id) => mutation(api.updateUser(Number(id), { is_active: false }));

// --- Сброс ----------------------------------------------------------------

export function clear() {
  session.profile = null;
  session.children = [];
  session.tasks.clear();
  session.people = [];
  session.mail = { inbox: [], sent: [], unread: 0 };
  session.journals = {};
  session.journalKeys = [];
  session.journalStudents = {};
  session.workTypes = [];
  session.attendance = {};
  session.classKeys = {};
  session.schedTeachers = {};
  session.teacherBySubject = {};
  session.school = {};
  session.students = [];
  session.active = false;

  // Возвращаем демо-структуры: серверные данные не должны утекать в демо.
  const restore = (target, source) => {
    Object.keys(target).forEach((key) => delete target[key]);
    Object.assign(target, JSON.parse(JSON.stringify(source)));
  };
  restore(STUDENTS, DEMO.students);
  restore(GRADES, DEMO.grades);
  restore(HOMEWORK, DEMO.homework);
  restore(SUBJECTS, DEMO.subjects);
  restore(CLASSES, DEMO.classes);
  restore(SCHEDULES, DEMO.schedules);
  TEACHERS.length = 0;
  DEMO.teachers.forEach((t) => TEACHERS.push(JSON.parse(JSON.stringify(t))));
}
