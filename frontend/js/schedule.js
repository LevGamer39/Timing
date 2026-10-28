import { fragment } from './templates.js';
import * as store from './store.js';
import { esc, icon, toast, sheet, weekday } from "./ui.js";
import { header, confirmSheet } from "./common.js";

import { shortName, subjName } from "./shared/admin.js";

const DAY_SHORT = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

let schedDay = null;

function scheduleView({ params, go, rerender }) {
  const cls = store.classes();
  if (!Object.keys(cls).length) return { html: header() + '<h1 class="title-l">Расписание</h1><div class="card pad empty-state"><p>Классов пока нет</p><a class="text-action" href="#/classes">Открыть классы</a></div>' };
  const key = params[0] && cls[params[0]] ? params[0] : Object.keys(cls)[0];
  const plan = store.scheduleOf(key);
  const nDays = 7;
  if (schedDay == null || schedDay >= nDays) schedDay = Math.min(weekday(store.NOW), nDays - 1);
  const sd = schedDay;
  const available =store.teachers().flatMap((t) => t.load.filter(([, ks]) => ks.includes(key)).map(([s]) => s));

  const allTeachers = store.teachers();
  const teacherName = (id) => (allTeachers.find((t) => t.id === id) || {}).name || '';
  const whoTeaches = (s, d, n) => teacherName(store.lessonTeacher(key, d, n)) || store.teacherFor(s, key);

  const cell = (d, n) => {
    const time = store.lessonTimes(d)[n];
    if (!time) return '<span class="muted">—</span>';
    const s = plan[d] && plan[d][n];
    if (!s) return ("<button class=\"sch-cell empty\" type=\"button\" data-d=\"" + (d) + "\" data-n=\"" + (n) + "\" aria-label=\"" + (DAY_SHORT[d]) + ", " + (n + 1) + " урок: добавить\">+</button>");
    const t = whoTeaches(s, d, n);
    const manual = !!store.lessonTeacher(key, d, n);
    return ("<button class=\"sch-cell\" type=\"button\" data-d=\"" + (d) + "\" data-n=\"" + (n) + "\"><b>" + (esc(subjName(s))) + "</b><small class=\"sch-time\">" + time + "</small><span>" + (t ? esc(shortName(t)) + (manual ? ' ✎' : '') : ("<span style=\"color: var(--danger)\">нет учителя</span>")) + " · " + (esc(store.SUBJECTS[s] ? store.SUBJECTS[s].room : '')) + "</span></button>");
  };
  return {
    html: fragment("schedule", "sc", [header(), Object.values(cls).map((c) => ("<option value=\"" + (c.key) + "\" " + (c.key === key ? 'selected' : '') + ">" + (esc(c.name)) + "</option>")).join(''), esc(store.currentPeriod().name), DAY_SHORT.slice(0, nDays).map((d, i) => ("<button type=\"button\" data-sday=\"" + (i) + "\" aria-pressed=\"" + (i === sd) + "\">" + (d) + "</button>")).join(''), store.lessonTimes(sd).map((tm, n) => ("<div class=\"sch-row\"><span class=\"t\"><b>" + (n + 1) + "</b></span>" + (cell(sd, n)) + "</div>")).join(''), esc(cls[key].name), DAY_SHORT.slice(0, nDays).map((d) => ("<th style=\"padding: 0 12px; text-align: left\">" + (d) + "</th>")).join(''), Array.from({ length: Math.max(...DAY_SHORT.map((_, d) => store.bells(d).length)) }, (_, n) => ("<tr><td class=\"t\"><b>" + (n + 1) + "</b></td>" + (DAY_SHORT.slice(0, nDays).map((_, d) => ("<td>" + (cell(d, n)) + "</td>")).join('')) + "</tr>")).join(''), icon('plus', 18)]),
    mount(root) {
      const changeBells = (remove) => {
        const school = store.school();
        const sets = school.bellSets.filter((set) => school.bellDays.includes(set.id));
        const title = remove ? 'Убрать последний урок' : 'Добавить урок';
        sheet(fragment('schedule', 'bell-action', [title, sets.map((set) => '<option value="' + esc(set.id) + '">' + esc(set.name) + '</option>').join(''), remove ? 'ghost schedule-delete' : '']), (el, close) => {
          const select = el.querySelector('#schedule-bell-set'), hint = el.querySelector('#bell-action-hint'), error = el.querySelector('#bell-action-error');
          const describe = () => {
            const set = sets.find((set) => set.id === select.value);
            const days = school.bellDays.flatMap((id, day) => id === set.id ? [DAY_SHORT[day]] : []);
            hint.textContent = (remove ? 'Будет удалён последний пустой урок и его звонок' : 'Будет добавлен урок и его звонок') + ' для всей школы. Дни: ' + days.join(', ') + '. ' + (remove ? 'Занятый урок удалить нельзя. Остальные уроки и оценки сохранятся.' : 'Предметы для каждого класса выбираются в таблице расписания.');
            error.hidden = true;
          };
          select.addEventListener('change', describe); describe();
          el.querySelector('form').addEventListener('submit', (event) => {
            event.preventDefault();
            const day = store.school().bellDays.indexOf(select.value);
            const message = day < 0 ? 'Набор больше не назначен дням. Откройте действие заново.' : remove ? store.removeLastBell(day) : store.appendBell(day);
            if (message) { error.textContent = message; error.hidden = false; return; }
            close(); rerender(); toast(remove ? 'Лишний урок убран' : 'Урок добавлен');
          });
        });
      };
      root.querySelector('#add-lesson-row').addEventListener('click', () => changeBells(false));
      root.querySelector('#remove-lesson-row').addEventListener('click', () => changeBells(true));
      root.querySelector('#sc').addEventListener('change', (e) => go('schedule/' + e.target.value));
      root.querySelectorAll('[data-sday]').forEach((b) => b.addEventListener('click', () => { schedDay = Number(b.dataset.sday); rerender(); }));
      root.querySelectorAll('[data-d]').forEach((b) => b.addEventListener('click', () => {
        const d = Number(b.dataset.d), n = Number(b.dataset.n);
        const cur = plan[d] && plan[d][n];
        const others = Object.keys(store.SUBJECTS).filter((k) => !available.includes(k));
        sheet(fragment("schedule", "lf", [DAY_SHORT[d], n + 1, esc(cls[key].name), store.lessonTimes(d)[n], esc(cls[key].name), [...new Set(available)].map((s) => ("<option value=\"" + (s) + "\" " + (s === cur ? 'selected' : '') + ">" + (esc(subjName(s))) + "</option>")).join(''), others.map((s) => ("<option value=\"" + (s) + "\" " + (s === cur ? 'selected' : '') + ">" + (esc(subjName(s))) + "</option>")).join('')]), (el, close) => {
          const ss = el.querySelector('#ss'), st = el.querySelector('#st'), hint = el.querySelector('#st-h');
          const fill = () => {
            const s = ss.value;
            const def = s ? store.teacherFor(s, key) : '';
            const manual = s === cur ? store.lessonTeacher(key, d, n) : '';
            st.disabled = !s;
            st.innerHTML = ("<option value=\"\">" + (def ? 'По нагрузке: ' + esc(shortName(def)) : 'Выберите учителя') + "</option>") +
              allTeachers.map((t) => ("<option value=\"" + (t.id) + "\" " + (t.id === manual ? 'selected' : '') + ">" + (esc(t.name)) + "</option>")).join('');
            hint.textContent = !s ? '' : def ? 'По умолчанию — учитель по нагрузке. Выберите другого, если урок ведёт он.' : 'По нагрузке этот предмет в классе никто не ведёт — выберите учителя.';
          };
          ss.addEventListener('change', fill); fill();
          const remove = el.querySelector('#delete-schedule-lesson');
          remove.hidden = !cur;
          remove.addEventListener('click', () => confirmSheet('Удалить урок?', DAY_SHORT[d] + ', ' + (n + 1) + ' урок · ' + cls[key].name + '. Остальные уроки останутся на своих местах. Оценки и записи журнала сохранятся.', 'Удалить', () => {
            const error = store.removeScheduleLesson(key, d, n);
            if (error) return toast(error);
            close(); rerender(); toast('Урок удалён из расписания');
          }, true));
          el.querySelector('#lf').addEventListener('submit', (e) => {
            e.preventDefault();
            const error = store.scheduleAssignmentError(ss.value, key, st.value, d, n);
            const errorEl = el.querySelector('#schedule-err');
            errorEl.hidden = !error; errorEl.textContent = error;
            if (error) { st.focus(); return; }
            while (plan.length < 7) plan.push([]);
            const day = plan[d];
            while (day.length <= n) day.push(null);
            day[n] = ss.value || null;
            while (day.length && !day[day.length - 1]) day.pop();
            if (!store.saveScheduleLesson(key, plan, d, n, ss.value ? st.value : '')) return toast(store.SAVE_ERROR);
            close(); rerender(); toast('Расписание ' + cls[key].name + ' сохранено');
          });
        });
      }));    }
  };
}
export { scheduleView };
