import { fragment } from './templates.js';
import * as store from './store.js';
import { esc, dayMonth, DAYS, weekday, plural, mailDate } from "./ui.js";
import { header } from "./common.js";

import { shortName } from "./shared/admin.js";

function adminHomeView() {
  const acc = store.account();
  const now = store.NOW;
  const cls = Object.values(store.classes());
  const students = cls.flatMap((c) => c.students.map((s) => Object.assign({ cls: c.name, key: c.key }, s)));
  const waiting = students.filter((s) => s.status === 'wait');
  const teachers = store.teachers();
  const journals = store.isServer() ? [] : store.journalKeys().map((k) => ({ k, done: store.finalsComplete(store.journal(k.key)) }));
  const period = store.currentPeriod();
  const unread = store.inbox().filter((m) => m.unread);

  return {
    html: fragment("admin-home", "stack", [header(), DAYS[weekday(now)], dayMonth(now), esc(store.SCHOOL.name), cls.length, plural(cls.length, 'класс', 'класса', 'классов'), esc(acc.first), cls.length, plural(cls.length, 'класс', 'класса', 'классов'), students.length, plural(students.length, 'ученик', 'ученика', 'учеников'), teachers.length, plural(teachers.length, 'учитель', 'учителя', 'учителей'), waiting.length, waiting.length, waiting.length ? waiting.map((s) => fragment("admin-home", "list-row", [s.key, esc(s.name), esc(s.cls), 'Приглашение ожидает активации'])).join('') : (students.length ? '<div class="empty-day">Все ученики уже вошли</div>' : '<div class="empty-day empty-state"><p>Учеников пока нет</p><a class="text-action" href="/classes">Открыть классы</a></div>'), esc(period.name), (period.to ? dayMonth(store.parse(period.to)) : 'весь год'), journals.map(({ k, done }) => ("<div class=\"list-row\"><span class=\"grow\"><b>" + (esc(k.cls)) + " · " + (esc(k.subj)) + "</b><br><span class=\"muted small\">" + (esc(store.teacherFor(k.subjectKey, store.classKey(k.cls)) ? shortName(store.teacherFor(k.subjectKey, store.classKey(k.cls))) : 'Учитель не назначен')) + "</span></span>" + (done ? ("<span class=\"pill ok\">Выставлены</span>") : ("<span class=\"pill wait\">Не выставлены</span>")) + "</div>")).join('') || (store.isServer() ? '<div class="empty-day empty-state"><p>Журналы ведут учителя в своём разделе</p><a class="text-action" href="/teachers">Открыть учителей</a></div>' : '<div class="empty-day empty-state"><p>Журналов пока нет</p><a class="text-action" href="/teachers">Открыть учителей</a></div>'), unread.length ? ("<span class=\"pill accent\">" + (unread.length) + "</span>") : '', unread.length ? unread.map((m) => { const p = store.person(m.from); return fragment("admin-home", "list-row-2", [m.id, esc(p.name), esc(m.subject), esc(mailDate(m.date, now))]); }).join('') : ("<div class=\"empty-day empty-state\"><p>Новых писем нет</p><a class=\"text-action\" href=\"/mail\">Открыть почту</a></div>")])
  };
}
export { adminHomeView };
