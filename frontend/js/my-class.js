import { fragment } from './templates.js';
import * as store from './store.js';
import { normalizedBounds } from "./grading.js";
import { esc, icon, zone, fmt, dayMonth, dayMonthShort, DAYS, weekday, plural, toast, iso, weighted, sheet } from "./ui.js";
import { header } from './common.js';
import { fileChip, bindFilePreviews } from './shared/file-preview.js';
import { createFileList } from './shared/file-upload.js';
import { absenceBlock } from './shared/attendance.js';
import { periodLabel, classTab, setClassTab } from "./shared/teacher.js";

const deadlineText = (value) => new Date(value).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' });

function mountHomework(cls, root) {
  const box = root.querySelector('#hw-box');
  if (!box) return;
  const render = (items) => {
    const mine = (items || []).filter((a) => String(a.class_id) === String(cls.key));
    box.innerHTML = '<div class="row" style="justify-content: space-between; align-items: center">'
      + '<h2 class="h2" style="font-size: 18px; margin: 0">Задания классу</h2>'
      + '<button class="btn sm" type="button" id="hw-add">Выдать задание</button></div>'
      + (mine.length ? mine.map((a) => '<article class="card pad stack-sm" style="gap: 4px">'
        + '<div class="row" style="gap: 8px"><b class="grow">' + esc(a.title) + '</b><span class="pill">' + (a.submitted_count || 0) + '/' + (a.total_count || 0) + '</span></div>'
        + '<p class="hint" style="margin: 0">' + esc(a.subject) + ' · до ' + deadlineText(a.deadline) + '</p>'
        + (a.description ? '<p style="margin: 0; font-size: 14px">' + esc(a.description) + '</p>' : '')
        + ((a.files && a.files.length) ? '<div class="file-row">' + a.files.map(fileChip).join('') + '</div>' : '')
        + '<div class="row" style="justify-content: flex-end"><button class="text-action" type="button" data-hw-open="' + a.id + '">Кто сдал</button>'
        + '<button class="text-action" type="button" data-hw-del="' + a.id + '">Удалить</button></div>'
        + '</article>').join('')
        : '<div class="card pad"><p class="muted" style="margin: 0">Заданий пока нет. Выдайте первое — оно появится у учеников в дневнике.</p></div>');
    box.querySelector('#hw-add')?.addEventListener('click', () => createSheet());
    bindFilePreviews(box);
    box.querySelectorAll('[data-hw-del]').forEach((b) => b.addEventListener('click', () => {
      store.assignmentDelete(b.dataset.hwDel).then((result) => {
        if (typeof result === 'string') return toast(result);
        toast('Задание удалено'); reload();
      }).catch(() => toast(store.SAVE_ERROR));
    }));
    box.querySelectorAll('[data-hw-open]').forEach((b) => b.addEventListener('click', () => progressSheet(b.dataset.hwOpen)));
  };

  const reload = () => store.assignmentList()
    .then((list) => render(list))
    .catch(() => { box.innerHTML = '<div class="card pad"><p class="muted" style="margin: 0">Не удалось загрузить задания.</p></div>'; });

  const createSheet = () => {
    sheet(fragment('my-class', 'hw-form', [esc(store.account()?.subject || ''), icon('clip', 18)]), (el, close) => {
      const err = el.querySelector('#ah-err');
      const fail = (text) => { err.textContent = text; err.hidden = false; };
      const uploader = createFileList({
        input: el.querySelector('#ah-file'),
        drop: el.querySelector('#ah-f'),
        list: el.querySelector('#ah-files'),
      });
      const deadline = el.querySelector('#ah-deadline');
      const note = el.querySelector('#ah-load');
      const minutesLabel = (m) => (m >= 60 ? ((m / 60) % 1 ? (m / 60).toFixed(1) : m / 60) + ' ч' : m + ' мин');
      const paintLoad = () => {
        if (!note) return;
        if (!deadline.value) { note.hidden = true; return; }
        store.classWorkload(cls.key, iso(new Date(deadline.value))).then((data) => {
          if (!data) { note.hidden = true; return; }
          const bits = [];
          let state = 'load-ok';
          if (data.count) {
            state = data.heavy || data.count >= 3 ? 'load-heavy' : 'load-warn';
            bits.push('Уже ' + data.count + ' ' + plural(data.count, 'задание', 'задания', 'заданий')
              + (data.subjects.length ? ' (' + data.subjects.map(esc).join(', ') + ')' : '')
              + ', ~' + minutesLabel(data.minutes) + '.');
            if (data.heavy) bits.push('Рядом крупная сдача.');
            const free = (data.nearby || []).filter((d) => d.minutes < data.minutes).slice(0, 3);
            if (state === 'load-heavy' && free.length) {
              bits.push('Свободнее: ' + free.map((d) => '<button type="button" class="text-action" data-load-day="'
                + d.date + '">' + dayMonthShort(new Date(d.date + 'T00:00:00')) + '</button>').join(' · '));
            }
          } else {
            bits.push('На этот день других заданий нет.');
          }
          note.className = 'load-note ' + state;
          note.innerHTML = bits.join(' ');
          note.hidden = false;
          note.querySelectorAll('[data-load-day]').forEach((b) => b.addEventListener('click', () => {
            const time = deadline.value.split('T')[1] || '23:59';
            deadline.value = b.dataset.loadDay + 'T' + time;
            paintLoad();
          }));
        }).catch(() => { note.hidden = true; });
      };
      let loadTimer;
      deadline?.addEventListener('change', () => {
        clearTimeout(loadTimer);
        loadTimer = setTimeout(paintLoad, 300);
      });
      el.querySelector('#ah-f').addEventListener('submit', (e) => {
        e.preventDefault();
        const title = el.querySelector('#ah-title').value.trim();
        const subject = el.querySelector('#ah-subject').value.trim();
        const raw = el.querySelector('#ah-deadline').value;
        if (!title) return fail('Введите задание.');
        if (!subject) return fail('Укажите предмет.');
        if (!raw) return fail('Укажите срок сдачи.');
        if (uploader.pending()) return fail('Подождите, файлы ещё загружаются.');
        store.assignmentCreate({
          title,
          subject,
          description: el.querySelector('#ah-desc').value.trim() || null,
          deadline: new Date(raw).toISOString(),
          class_id: Number(cls.key),
          priority: el.querySelector('#ah-priority').value,
          files: uploader.files.filter((f) => f.token).map((f) => f.token),
        }).then((result) => {
          if (typeof result === 'string') return fail(result);
          close(); reload(); toast('Задание выдано');
        }).catch((error) => fail((error && error.message) || store.SAVE_ERROR));
      });
    });
  };

  const progressSheet = (id) => {
    Promise.all([store.assignmentProgress(id), store.assignmentGrades(id).catch(() => [])]).then(([data, grades]) => {
      const existing = new Map((grades || []).map((g) => [String(g.student_id), g.value]));
      const students = data.students || [];
      const total = data.total ?? ((data.completed || 0) + (data.started || 0) + (data.not_started || 0) || students.length);
      const doneCount = students.filter((s) => s.status === 'done').length;
      const label = (status) => ({ accepted: 'Принято', done: 'Сдал', overdue: 'Ожидает', in_progress: 'В работе' }[status] || 'Нет');
      sheet('<h2 class="h2" style="font-size: 20px">' + esc(data.title) + '</h2>'
        + '<p class="hint">' + (data.completed || 0) + ' из ' + total + ' сдали · принято ' + (data.accepted || 0) + ' · ' + esc(data.subject) + '</p>'
        + '<div class="stack-sm">' + (students.length ? students.map((s) => '<div class="row" style="gap: 8px; min-height: 40px">'
          + '<span class="grow">' + esc(s.student_name) + '</span>'
          + '<span class="hint" style="min-width: 60px">' + label(s.status) + '</span>'
          + (s.status === 'done' ? '<button class="text-action" type="button" data-accept="' + s.student_id + '">Принять</button>' : '')
          + '<input class="input" type="number" min="1" max="5" step="1" inputmode="numeric" data-g="' + s.student_id + '" value="' + (existing.get(String(s.student_id)) ?? '') + '" aria-label="Оценка ' + esc(s.student_name) + '" style="width: 64px; padding: 6px 8px">'
          + '</div>').join('') : '<p class="muted" style="margin: 0">В классе нет учеников.</p>') + '</div>'
        + (doneCount ? '<button class="btn sm block" type="button" id="ah-accept" style="margin-top: 12px">Принять всех, кто отметил (' + doneCount + ')</button>' : '')
        + (students.length ? '<button class="btn sm block" type="button" id="ah-grades" style="margin-top: 12px">Сохранить оценки</button>' : ''), (el, close) => {
        const accept = (ids) => {
          store.assignmentAccept(id, ids ? { student_ids: ids } : {}).then((result) => {
            if (typeof result === 'string') return toast(result);
            close(); progressSheet(id); reload();
            toast(ids ? 'Работа принята' : 'Отмеченные работы приняты');
          }).catch(() => toast(store.SAVE_ERROR));
        };
        el.querySelector('#ah-accept')?.addEventListener('click', () => accept(null));
        el.querySelectorAll('[data-accept]').forEach((b) => b.addEventListener('click', () => accept([Number(b.dataset.accept)])));
        el.querySelector('#ah-grades')?.addEventListener('click', () => {
          const items = [...el.querySelectorAll('[data-g]')]
            .filter((i) => i.value !== '')
            .map((i) => ({ student_id: Number(i.dataset.g), value: Number(i.value) }));
          if (!items.length) return toast('Введите хотя бы одну оценку.');
          store.assignmentGrade(id, data.subject, items).then((result) => {
            if (typeof result === 'string') return toast(result);
            close(); toast('Оценки сохранены');
          }).catch(() => toast(store.SAVE_ERROR));
        });
      });
    }).catch(() => toast('Не удалось загрузить прогресс'));
  };

  reload();
}

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
  const periodFrom = period.from || '2000-01-01';
  const periodTo = period.to || today;
  const missedBy = Object.fromEntries(store.absencesBetween(cls.key, periodFrom, periodTo < today ? periodTo : today).map((x) => [x.name, x.lessons]));
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

  const hwBox = classTab === 'hw' ? '<div id="hw-box" class="stack-sm"><div class="card pad"><p class="muted" style="margin: 0">Загружаю…</p></div></div>' : null;
  const content = !rows.length ? '<div class="card pad empty-state"><p>Учеников пока нет</p><a class="text-action" href="/classes/' + cls.key + '">Открыть приглашения</a></div>' : classTab === 'att' ? ("<p class=\"muted\" style=\"margin: 0 4px\">Отметьте, кого нет · " + (DAYS[weekday(store.NOW)].toLowerCase()) + ", " + (dayMonth(store.NOW)) + "</p>") + attTable : classTab === 'hist' ? history.html : classTab === 'hw' ? hwBox : avgTable;

  return {
    html: fragment("my-class", "stack", [header(), periodLabel(), esc(cls.name), cls.key, rows.length, absentToday ? ("<span class=\"pill\">Сегодня отсутствуют: " + (absentToday) + "</span>") : '', risk.length ? ("<span class=\"pill\" style=\"background: var(--g2-bg); color: var(--g2)\" title=\"" + (esc(risk.map((x) => x.name).join(', '))) + "\">Выходит «2»: " + (risk.length) + " " + (plural(risk.length, 'ученик', 'ученика', 'учеников')) + "</span>") : '', classTab === 'att', classTab === 'hist', classTab === 'avg', classTab === 'hw', content]),
    mount(root) {
      root.querySelectorAll('[data-tab]').forEach((b) => b.addEventListener('click', () => { setClassTab(b.dataset.tab); rerender(); }));
      if (classTab === 'hw') mountHomework(cls, root);
      if (history && rows.length && classTab === 'hist') history.mount(root, rerender);
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
