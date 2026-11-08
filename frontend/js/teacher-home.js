import { fragment } from './templates.js';
import * as store from './store.js';

import { esc, icon, dayMonth, DAYS, weekday, plural, mailDate, parse, iso } from "./ui.js";
import { header } from './common.js';

import { periodEnd } from "./shared/teacher.js";

function teacherHomeView() {
  const acc = store.account();
  const now = store.NOW;
  const nowMin = now.getHours() * 60 + now.getMinutes();
  const toMin = (t) => { const [h, m] = t.split(':').map(Number); return h * 60 + m; };
  const nextIdx = store.todayLessons().findIndex((l) => toMin(l.end) > nowMin);
  const unread = store.inbox().filter((m) => m.unread);
  const daysLeft = store.periodDaysLeft();
  const noFinals = store.journalKeys().filter((k) => !store.finalsComplete(store.journal(k.key)));

  const lessonRow = (l, i) => {
    const past = toMin(l.end) <= nowMin;
    const next = i === nextIdx;
    const inMin = toMin(l.start) - nowMin;
    return fragment("teacher-home", "tl", [next ? ' now' : '', past ? ' past' : '', esc(l.time), past ? ("<span class=\"pill\">прошёл</span>") : '', next ? ("<span class=\"pill accent\">через " + (inMin) + " мин</span>") : '', esc(l.cls), esc(l.subj), esc(l.room), next ? '' : 'ghost ', '/journal/' + l.key + '/' + iso(now)]);
  };

  return {
    html: fragment("teacher-home", "stack", [header(), DAYS[weekday(now)], dayMonth(now), esc(acc.first), store.todayLessons().length, plural(store.todayLessons().length, 'урок', 'урока', 'уроков'), store.todayLessons().map(lessonRow).join('') || '<div class="empty-day empty-state"><p>Сегодня уроков нет</p><a class="text-action" href="/journal">Открыть журнал</a></div>', unread.length ? ("<span class=\"pill accent\">" + (unread.length) + "</span>") : '', unread.length ? unread.map((m) => {
          const p = store.person(m.from);
          return fragment("teacher-home", "list-row", [m.id, esc(p.name), esc(p.role), esc(m.subject), esc(mailDate(m.date, now))]);
        }).join('') : ("<div class=\"empty-day empty-state\"><p>Новых писем нет</p><a class=\"text-action\" href=\"/mail\">Открыть почту</a></div>"), store.periodWord('gen'), daysLeft, plural(daysLeft, 'день', 'дня', 'дней'), dayMonth(parse(periodEnd())), noFinals.length ? 'Итоговые не выставлены: ' + noFinals.map((k) => k.cls + ' ' + k.subj.toLowerCase()).join(', ') : 'Все итоговые выставлены', noFinals.length ? ("<a class=\"btn ghost sm\" style=\"align-self: flex-start; margin-top: 8px\" href=\"/journal/" + (noFinals[0].key) + "\">К итоговым оценкам " + (icon('next', 18)) + "</a>") : ''])
  };
}
export { teacherHomeView };
