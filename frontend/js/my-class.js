import { fragment } from './templates.js';
import * as store from './store.js';
import { normalizedBounds } from "./grading.js";
import { esc, zone, fmt, dayMonth, DAYS, weekday, plural, toast, iso, weighted } from "./ui.js";
import { header } from './common.js';
import { absenceBlock } from './shared/attendance.js';
import { periodLabel, classTab, setClassTab } from "./shared/teacher.js";

function myClassView({ rerender }) {
  const cls = store.homeroom();
  if (!cls) return { html: header() + ("<h1 class=\"title-l\">Мой класс</h1><div class=\"card pad\">Классное руководство пока не назначено.</div>") };
  const subjKeys = [];
  store.scheduleOf(cls.key).flat().filter(Boolean).forEach((k) => { if (!subjKeys.includes(k)) subjKeys.push(k); });
  const available = store.journalKeys().filter((k) => k.cls === cls.name);
  const blank = { names: cls.students.map((s) => s.name), lessons: [], grades: {}, finals: {} };
  const alg = available.some((k) => k.subjectKey === 'algebra') ? store.journal(cls.key + '-algebra') : blank;
  const geo = available.some((k) => k.subjectKey === 'geometry') ? store.journal(cls.key + '-geometry') : blank;
  const artem = store.gradesOf('artem');
  const today = iso(store.NOW);
  const todayPlan = store.scheduleOf(cls.key)[weekday(store.NOW)] || [];
  const att = store.attendance(cls.key, today);
  const period = store.currentPeriod();
  const missedBy = Object.fromEntries(store.absencesBetween(cls.key, period.from, today < period.to ? today : period.to).map((x) => [x.name, x.lessons]));
  const history = classTab === 'hist' ? absenceBlock([cls.key]) : null;
  let seed = 17;
  const rnd = () => { seed = (seed * 9301 + 49297) % 233280; return seed / 233280; };
  const rows = alg.names.map((name, r) => {
    const base = store.rowAvg(alg, r) ?? 60;
    const avgs = subjKeys.map((k) => {
      if (k === 'algebra') return store.rowAvg(alg, r);
      if (k === 'geometry') return store.rowAvg(geo, r);
      if (name === 'Белов Артём') return weighted(artem.filter((g) => g.subj === k));
      if (k === 'pe') return 80 + rnd() * 20;
      return Math.max(25, Math.min(100, base + (rnd() - 0.5) * 30));
    });
    const todayN = todayPlan.filter((s, n) => s && att[r + '|' + (n + 1)]).length;
    const absent = missedBy[name] || 0;
    const nums = avgs.filter((a) => a != null);
    return { name, avgs, absent, todayN, total: nums.reduce((a, b) => a + b, 0) / nums.length };
  });
  const risk = rows.filter((x) => x.avgs.some((a) => a != null && a < normalizedBounds()[0]));
  const short = { algebra: 'Алг', geometry: 'Геом', russian: 'Рус', literature: 'Лит', english: 'Англ', physics: 'Физ', chemistry: 'Хим', biology: 'Био', history: 'Ист', informatics: 'Инф', pe: 'Физ-ра' };
  const lessonsToday = todayPlan.map((s, n) => ({ s, n: n + 1 })).filter((x) => x.s);
  const absentToday = rows.filter((x) => x.todayN).length;

  const attTable = fragment("my-class", "only-mobile", [lessonsToday.map(({ s, n }) => ("<span class=\"pill\">" + (n) + " · " + (short[s] || esc(store.SUBJECTS[s].name)) + " " + (store.lessonTimes(store.NOW)[n - 1].split('–')[0]) + "</span>")).join(''), rows.map((x, r) => fragment("my-class", "p-card", [esc(x.name), r, x.todayN === lessonsToday.length ? 'Отменить' : 'Н весь день', lessonsToday.map(({ n }) => ("<button type=\"button\" class=\"att" + (att[r + '|' + n] ? ' on' : '') + "\" data-r=\"" + (r) + "\" data-n=\"" + (n) + "\" aria-pressed=\"" + (!!att[r + '|' + n]) + "\" aria-label=\"" + (esc(x.name)) + ", " + (n) + " урок: " + (att[r + '|' + n] ? 'не был' : 'был') + "\">" + (att[r + '|' + n] ? 'Н' : n) + "</button>")).join('')])).join(''), esc(cls.name), lessonsToday.map(({ s, n }) => ("<th class=\"num\">" + (n) + " · " + (short[s] || esc(store.SUBJECTS[s].name)) + "<br><span class=\"muted small\" style=\"font-weight: 500\">" + (store.lessonTimes(store.NOW)[n - 1].split('–')[0]) + "</span></th>")).join(''), rows.map((x, r) => fragment("my-class", "num", [esc(x.name), lessonsToday.map(({ n }) => fragment("my-class", "num-2", [att[r + '|' + n] ? ' on' : '', r, n, !!att[r + '|' + n], esc(x.name), n, att[r + '|' + n] ? 'не был' : 'был', att[r + '|' + n] ? 'Н' : '·'])).join(''), r, x.todayN === lessonsToday.length ? 'Отменить' : 'Н на весь день'])).join('')]);

  const avgTable = fragment("my-class", "card", [rows.map((x) => fragment("my-class", "p-card-2", [esc(x.name), x.absent, zone(x.total), fmt(x.total), x.avgs.map((a, i) => a == null ? '' : ("<span class=\"pill " + (zone(a)) + "-pill\">" + (short[subjKeys[i]] || esc(store.SUBJECTS[subjKeys[i]].name)) + " <b>" + (fmt(a)) + "</b></span>")).join('')])).join(''), esc(cls.name), subjKeys.map((k) => ("<th class=\"num\" title=\"" + (esc(store.SUBJECTS[k].name)) + "\">" + (short[k] || esc(store.SUBJECTS[k].name)) + "</th>")).join(''), rows.map((x) => ("\n        <tr><td style=\"font-weight: 600\">" + (esc(x.name)) + "</td>\n        " + (x.avgs.map((a) => ("<td class=\"num\">" + (a == null ? ("<span class=\"muted\">·</span>") : ("<span class=\"g " + (zone(a)) + "\" style=\"min-width: 52px; height: 28px; line-height: 28px; font-size: 13px\">" + (fmt(a)) + "</span>")) + "</td>")).join('')) + "\n        <td class=\"num\">" + (x.absent ? x.absent : ("<span class=\"muted\">0</span>")) + "</td>\n        <td class=\"num\"><b>" + (fmt(x.total)) + "</b></td></tr>")).join(''), store.periodWord()]);

  return {
    html: fragment("my-class", "stack", [header(), periodLabel(), esc(cls.name), cls.key, rows.length, absentToday ? ("<span class=\"pill\">Сегодня отсутствуют: " + (absentToday) + "</span>") : '', risk.length ? ("<span class=\"pill\" style=\"background: var(--g2-bg); color: var(--g2)\" title=\"" + (esc(risk.map((x) => x.name).join(', '))) + "\">Выходит «2»: " + (risk.length) + " " + (plural(risk.length, 'ученик', 'ученика', 'учеников')) + "</span>") : '', classTab === 'att', classTab === 'hist', classTab === 'avg', !rows.length ? '<div class="card pad empty-state"><p>Учеников пока нет</p><a class="text-action" href="#/classes/' + cls.key + '">Открыть приглашения</a></div>' : classTab === 'att' ? ("<p class=\"muted\" style=\"margin: 0 4px\">Отметьте, кого нет · " + (DAYS[weekday(store.NOW)].toLowerCase()) + ", " + (dayMonth(store.NOW)) + "</p>") + attTable : classTab === 'hist' ? history.html : avgTable]),
    mount(root) {
      root.querySelectorAll('[data-tab]').forEach((b) => b.addEventListener('click', () => { setClassTab(b.dataset.tab); rerender(); }));
      if (history && rows.length) history.mount(root, rerender);
      const saveAtt = (fn) => { const m = store.attendance(cls.key, today); const message = fn(m); if (!store.setAttendance(cls.key, today, m)) return toast(store.SAVE_ERROR); rerender(); if (message) toast(message); };
      root.querySelectorAll('.att').forEach((b) => b.addEventListener('click', () => saveAtt((m) => {
        const k = b.dataset.r + '|' + b.dataset.n;
        if (m[k]) delete m[k]; else m[k] = true;
      })));
      root.querySelectorAll('[data-day]').forEach((b) => b.addEventListener('click', () => saveAtt((m) => {
        const r = b.dataset.day;
        const all = lessonsToday.every(({ n }) => m[r + '|' + n]);
        lessonsToday.forEach(({ n }) => { if (all) delete m[r + '|' + n]; else m[r + '|' + n] = true; });
        return all ? 'Пропуск отменён' : rows[r].name + ': «Н» на весь день';
      })));
    }
  };
}
export { myClassView };
