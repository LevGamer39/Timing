import { fragment } from '../templates.js';
import * as store from '../store.js';
import { esc, icon, dayMonth, DAYS, weekday, parse, iso, plural, toast, sheet } from '../ui.js';

const picked = {};

store.onContextChange(() => { Object.keys(picked).forEach((k) => delete picked[k]); adminCls = 'all'; });

const DAY_SHORT = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

const dayLabel = (date) => { const d = parse(date); return DAY_SHORT[weekday(d)] + ', ' + dayMonth(d); };

const lessonsText = (a) => a.allDay ? 'весь день' : a.lessons.map((l) => l.n + ' · ' + store.SUBJECTS[l.subj].name).join(', ');

const lessonsWord = (n) => n + ' ' + plural(n, 'урок', 'урока', 'уроков');

function downloadCsv(keys, title) {
  const all = store.classes();
  const p = store.currentPeriod();
  const today = iso(store.NOW);
  const to = today < p.to ? today : p.to;
  const rows = [['Класс', 'Дата', 'День недели', 'Ученик', 'Пропущено уроков', 'Какие уроки']];
  keys.forEach((k) => store.schoolDaysOf(k, p.from, to).forEach((d) => store.absencesOn(k, d).forEach((a) => rows.push([
    all[k].name, d.split('-').reverse().join('.'), DAYS[weekday(parse(d))], a.name, a.lessons.length, a.allDay ? 'весь день' : lessonsText(a)
  ]))));
  // «;» и BOM — иначе русский Excel откроет файл одной колонкой и кракозябрами.
  const csv = '﻿' + rows.map((r) => r.map((v) => '"' + String(v).replace(/"/g, '""') + '"').join(';')).join('\r\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  a.download = 'Пропуски ' + title + ' — ' + p.name + '.csv';
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  toast('Файл скачан: ' + (rows.length - 1) + ' ' + plural(rows.length - 1, 'запись', 'записи', 'записей'));
}

function absenceBlock(clsKeys, school = null) {
  const all = store.classes();
  const p = store.currentPeriod();
  const today = iso(store.NOW);
  const to = today < p.to ? today : p.to;
  const days = [...new Set(clsKeys.flatMap((k) => store.schoolDaysOf(k, p.from, to)))].sort();
  const id = clsKeys.join(',');
  if (!days.includes(picked[id])) picked[id] = days[days.length - 1];
  const day = picked[id];
  const i = days.indexOf(day);
  const many = clsKeys.length > 1;

  const dayList = clsKeys.map((k) => {
    const list = day ? store.absencesOn(k, day) : [];
    if (many && !list.length) return '';
    return `${many ? ("<button class=\"ab-cls\" type=\"button\" data-cls=\"" + (k) + "\">" + (esc(all[k].name)) + (icon('next', 16)) + "</button>") : ''}
      ${list.length ? list.map((a) => ("<div class=\"ab-day-row\"><b>" + (esc(a.name)) + "</b>\n        <span class=\"ab-lessons\">" + (a.allDay
          ? ("<span class=\"ab-l all\">Весь день · " + (lessonsWord(a.lessons.length)) + "</span>")
          : a.lessons.map((l) => ("<span class=\"ab-l\"><b>" + (l.n) + "</b>" + (esc(store.SUBJECTS[l.subj].name)) + "<small>" + (store.lessonTimes(day)[l.n - 1].split('–')[0]) + "</small></span>")).join('')) + "</span></div>")).join('')
        : ("<p class=\"muted\" style=\"margin: 0; padding: 16px\">Все были на уроках.</p>")}`;
  }).join('') || ("<p class=\"muted\" style=\"margin: 0; padding: 16px\">Все были на уроках.</p>");

  const summary = many
    ? clsKeys.map((k) => { const s = store.absencesBetween(k, p.from, to); const n = s.reduce((a, x) => a + x.lessons, 0); return { name: all[k].name, n, kids: s.filter((x) => x.lessons).length }; })
      .map((x, n) => fragment("shared-attendance", "list-row", [clsKeys[n], esc(x.name), x.n ? lessonsWord(x.n) + ' · ' + x.kids + ' ' + plural(x.kids, 'ученик', 'ученика', 'учеников') : 'без пропусков', icon('next', 18)])).join('')
    : (() => {
      const s = store.absencesBetween(clsKeys[0], p.from, to);
      const missed = s.filter((x) => x.lessons), clean = s.length - missed.length;
      return missed.map((x) => ("<div class=\"list-row ab-row\"><span class=\"grow\" style=\"font-weight: 600\">" + (esc(x.name)) + "</span><span class=\"muted small\">" + (lessonsWord(x.lessons)) + (x.days ? ' · ' + x.days + ' ' + plural(x.days, 'день', 'дня', 'дней') + ' целиком' : '') + "</span></div>")).join('')
        + (clean ? ("<p class=\"muted small\" style=\"margin: 0; padding: 12px 16px\">Без пропусков: " + (clean) + " " + (plural(clean, 'ученик', 'ученика', 'учеников')) + "</p>") : '');
    })();

  return {
    html: fragment("shared-attendance", "ab-prev", [i <= 0 ? 'disabled' : '', days.slice().reverse().map((d) => ("<option value=\"" + (d) + "\" " + (d === day ? 'selected' : '') + ">" + (dayLabel(d)) + (d === today ? ' · сегодня' : '') + "</option>")).join(''), i >= days.length - 1 ? 'disabled' : '', dayList, many ? 'За ' : esc(all[clsKeys[0]].name) + ' · за ', esc(p.name), dayMonth(parse(to)), school ? fragment("shared-attendance", "ab-tabs", [many, school.keys.map((k) => ("<button type=\"button\" data-cls=\"" + (k) + "\" aria-pressed=\"" + (!many && k === clsKeys[0]) + "\">" + (esc(all[k].name)) + "</button>")).join('')]) : '', summary, '', icon('download', 18)]),
    mount(root, rerender) {
      const set = (d) => { picked[id] = d; rerender(); };
      root.querySelector('#ab-day').addEventListener('change', (e) => set(e.target.value));
      root.querySelector('#ab-prev').addEventListener('click', () => set(days[i - 1]));
      root.querySelector('#ab-next').addEventListener('click', () => set(days[i + 1]));
      if (school) root.querySelectorAll('[data-cls]').forEach((b) => b.addEventListener('click', () => school.pick(b.dataset.cls)));
      root.querySelector('#ab-csv').addEventListener('click', () => {
        if (!school) return downloadCsv(clsKeys, all[clsKeys[0]].name);
        sheet(fragment("shared-attendance", "row", [icon('close', 20), esc(p.name), many ? '' : ' ghost', icon('download', 18), school.keys.map((k) => ("<button class=\"btn block" + (!many && k === clsKeys[0] ? '' : ' ghost') + "\" type=\"button\" data-dl=\"" + (k) + "\">" + (icon('download', 18)) + "Только " + (esc(all[k].name)) + "</button>")).join('')]), (el, close) => el.querySelectorAll('[data-dl]').forEach((b) => b.addEventListener('click', () => {
          const k = b.dataset.dl;
          downloadCsv(k === 'all' ? school.keys : [k], k === 'all' ? 'школа' : all[k].name);
          close();
        })));
      });
    }
  };
}

let adminCls = 'all';
export { absenceBlock, picked, adminCls, lessonsWord, dayLabel, DAY_SHORT, downloadCsv, lessonsText };

export const setAdminCls = (value) => { adminCls = value; };
