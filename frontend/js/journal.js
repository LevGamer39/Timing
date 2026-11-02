import { fragment } from './templates.js';
import * as store from './store.js';
import { score, storedScore, gradeScale, gradeMin, gradeRanges } from "./grading.js";
import { esc, icon, zone, mark, fmt, ddmm, dayMonth, DAYS, weekday, plural, toast, sheet, parse, iso } from "./ui.js";
import { header } from './common.js';

import { periodLabel, periodEnd, ui } from "./shared/teacher.js";

const typeShort = (t) => ({ lesson: '', home: 'дз', self: 'с/р', test: 'тест', kr: 'кр' }[t.id] ?? t.name.toLowerCase().slice(0, 6));

let draftKey = null, errorDrafts = new Map();
store.onContextChange(() => { draftKey = null; errorDrafts.clear(); });

function journalView({ params, go, rerender }) {
  let captureDraft = () => null, restoreDraft = () => {};
  const keys = store.journalKeys();
  if (!keys.length) return { html: header() + ("<h1 class=\"title-l\">Журнал</h1><div class=\"card pad\">" + (params[0] ? 'Журнал недоступен. ' : '') + "Журналов пока нет. Предметы и классы назначает администратор.</div>") };
  const unavailable = params[0] && !keys.some((k) => k.key === params[0]);
  if (params[0] && !unavailable) ui.key = params[0];
  if (!keys.some((k) => k.key === ui.key)) ui.key = keys[0].key;
  if (draftKey !== ui.key) { draftKey = ui.key; errorDrafts.clear(); }
  const j = store.journal(ui.key);
  const studentKeys = (store.classes()[store.classKey(j.cls)]?.students || []).map((s) => s.login);
  const types = store.workTypes();
  const lessonTime = (date) => store.scheduleOf(store.classKey(j.cls))[weekday(parse(date))].flatMap((s, n) => s === j.subjectKey ? [store.lessonTimes(date)[n]] : []).join(', ');
  if (ui.lesson == null || ui.lesson >= j.lessons.length) ui.lesson = j.lessons.length - 1;
  const requestedDate = unavailable ? null : params[1];
  const requestedLesson = requestedDate ? j.lessons.findIndex((l) => l.date === requestedDate) : -1;
  if (requestedLesson >= 0) { ui.lesson = requestedLesson; ui.sel = null; }
  const C = j.lessons.length; // индекс колонки «Итог»

  const cellHtml = (r, c) => {
    if (c === C) {
      const f = j.finals[r];
      return typeof f === 'number' ? ("<span class=\"g " + (zone(f)) + "\">" + (score(f)) + "</span>") : ("<span class=\"e\">·</span>");
    }
    const k = r + '|' + j.lessons[c].date;
    const v = j.grades[k];
    const dot = j.comments[k] ? ("<i class=\"cm\" title=\"Есть комментарий\"></i>") : '';
    if (v === undefined) return ("<span class=\"e\">·</span>");
    return ("<span class=\"g " + (zone(v)) + "\">" + (score(v)) + "</span>" + (dot));
  };
  const avgHtml = (r) => { const a = store.rowAvg(j, r); return a == null ? ("<span class=\"e\">·</span>") : ("<span class=\"g avg " + (zone(a)) + "\">" + (fmt(a)) + "</span>"); };

  const head = j.lessons.map((l, c) => {
    const t = store.typeOf(l.type);
    const short = typeShort(t);
    return ("<th class=\"" + (c === ui.lesson ? 'on' : '') + "\"><button type=\"button\" class=\"th-btn\" data-lesson=\"" + (c) + "\" aria-label=\"Урок " + (ddmm(l.date)) + ", " + (esc(t.name)) + "\">" + (ddmm(l.date)) + "<small>" + (short ? esc(short) + ' ×' + t.w : '&nbsp;') + "</small></button></th>");
  }).join('');

  const rows = j.names.map((n, r) => fragment("journal", "no", [r + 1, esc(n), j.lessons.map((l, c) => ("<td class=\"cell" + (c === ui.lesson ? ' col-on' : '') + "\" data-r=\"" + (r) + "\" data-c=\"" + (c) + "\" tabindex=\"-1\">" + (cellHtml(r, c)) + "</td>")).join(''), r, avgHtml(r), r, C, cellHtml(r, C)])).join('');

  return {
    captureDraft: () => captureDraft(),
    restoreDraft: (draft) => restoreDraft(draft),
    html: fragment("journal", "jk", [header(), store.journalKeys().map((k) => ("<option value=\"" + (k.key) + "\" " + (k.key === ui.key ? 'selected' : '') + ">" + (esc(k.cls)) + " · " + (esc(k.subj)) + "</option>")).join(''), periodLabel(), dayMonth(parse(periodEnd())), periodLabel(), icon('spark', 18), C > 2 ? ("<p class=\"hint only-mobile\" style=\"margin: 0 4px\">← Листайте таблицу влево — там прошлые уроки</p>") : '', esc(j.cls), esc(j.subj), head, rows, gradeRanges().map((r) => ("<span><span class=\"g g" + (r.mark) + "\">·</span> " + (r.from) + "–" + (r.to) + "</span>")).join(''), C]),
    mount(root) {
      const table = root.querySelector('.jt');
      const panel = root.querySelector('#panel');
      const box = root.querySelector('.j-box');
      box.scrollLeft = ui.scroll && ui.scroll.key === ui.key ? ui.scroll.x : box.scrollWidth;
      if (unavailable) toast('Журнал недоступен');
      if (requestedDate && !unavailable) {
        if (requestedLesson >= 0) root.querySelector(`[data-lesson="${requestedLesson}"]`)?.scrollIntoView?.({ block: 'nearest', inline: 'center' });
        else toast('Урока на эту дату в журнале нет');
      }
      box.addEventListener('scroll', () => { ui.scroll = { key: ui.key, x: box.scrollLeft }; }, { passive: true });
      const cell = (r, c) => table.querySelector(`.cell[data-r="${r}"][data-c="${c}"]`);
      const save = () => { const ok = store.saveJournal(j); if (!ok) toast(store.SAVE_ERROR); return ok; };
      let editing = null;
      let mobileCell = null;
      captureDraft = () => {
        const active = document.activeElement;
        const input = editing && cell(editing.r, editing.c)?.querySelector('.cell-in');
        const mobile = document.getElementById('grade-value');
        const position = editing || (mobile && mobileCell);
        const field = mobile || input;
        if (!field || !position) { editing = null; return null; }
        const draft = { ...position, studentKey: studentKeys[position.r], date: j.lessons[position.c]?.date, final: position.c === C, mobile: !!mobile, value: field.value, comment: document.getElementById('grade-comment')?.value, focus: active === field ? 'value' : active?.id, start: active?.selectionStart, end: active?.selectionEnd };
        editing = null;
        return draft;
      };
      restoreDraft = (draft) => {
        draft.r = studentKeys.indexOf(draft.studentKey);
        draft.c = draft.final ? C : j.lessons.findIndex((l) => l.date === draft.date);
        if (!cell(draft.r, draft.c)) return;
        if (draft.mobile) mobileEdit(draft.r, draft.c); else startEdit(draft.r, draft.c, draft.value);
        const field = draft.mobile ? document.getElementById('grade-value') : cell(draft.r, draft.c).querySelector('.cell-in');
        field.value = draft.value;
        const comment = document.getElementById('grade-comment');
        if (comment && draft.comment != null) comment.value = draft.comment;
        const focus = draft.focus === 'value' ? field : document.getElementById(draft.focus);
        if (focus) { focus.focus(); if (typeof draft.start === 'number' && focus.type !== 'number') focus.setSelectionRange(draft.start, draft.end); }
      };

      const lessonPanel = () => {
        const l = j.lessons[ui.lesson];
        if (!l) {
          panel.innerHTML = '<div class="empty-state"><p>В ' + store.periodWord('prep') + ' уроков пока нет</p><button class="text-action" type="button" data-empty-add>Добавить урок</button></div>';
          panel.querySelector('[data-empty-add]').addEventListener('click', () => root.querySelector('#add').click());
          return;
        }
        const graded = j.names.filter((_, r) => j.grades[r + '|' + l.date] !== undefined).length;
        panel.innerHTML = fragment("journal", "lt", [ddmm(l.date) + (lessonTime(l.date) ? ' · ' + lessonTime(l.date) : ''), DAYS[weekday(parse(l.date))].toLowerCase(), types.map((t) => ("<option value=\"" + (t.id) + "\" " + (t.id === l.type ? 'selected' : '') + ">" + (esc(t.name)) + " ×" + (t.w) + "</option>")).join(''), esc(l.hw), graded, j.names.length]);
        panel.querySelector('#lt').addEventListener('change', (e) => {
          if (e.target.value === '__new') { e.target.value = l.type; newTypeSheet((id) => { l.type = id; if (save()) rerender(); }); return; }
          l.type = e.target.value; if (!save()) return; rerender(); toast('Тип работы изменён');
        });
        panel.querySelector('#lh').addEventListener('change', (e) => { l.hw = e.target.value.trim(); if (save()) toast('Домашка сохранена'); });
        panel.querySelector('#del-lesson').addEventListener('click', () => confirmSheet(
          'Удалить урок ' + ddmm(l.date) + '?', graded ? 'Вместе с ним удалятся ' + graded + ' ' + plural(graded, 'оценка', 'оценки', 'оценок') + '.' : 'Оценок на этом уроке нет.', 'Удалить', () => {
            j.names.forEach((_, r) => { delete j.grades[r + '|' + l.date]; delete j.comments[r + '|' + l.date]; });
            j.lessons.splice(ui.lesson, 1);
            ui.lesson = Math.max(0, ui.lesson - 1); ui.sel = null;
            if (!save()) return; rerender(); toast('Урок удалён');
          }));
      };
      const cellPanel = (r, c) => {
        if (c === C) {
          const a = store.rowAvg(j, r);
          panel.innerHTML = fragment("journal", "eyebrow", [periodLabel(), esc(j.names[r]), a == null ? 'нет оценок' : fmt(a) + ' → ' + score(a), gradeMin(), gradeScale()]);
          return;
        }
        const l = j.lessons[c];
        const k = r + '|' + l.date;
        const v = j.grades[k];
        const t = store.typeOf(l.type);
        panel.innerHTML = ("\n<p class=\"eyebrow\">" + (ddmm(l.date)) + " · " + (esc(t.name)) + " ×" + (t.w) + "</p>\n<h2 class=\"h2\" style=\"font-size: 20px\">" + (esc(j.names[r])) + "</h2>\n<div class=\"row\">" + (v === undefined ? ("<span class=\"muted\">Оценки нет — введите число от " + (gradeMin()) + " до " + (gradeScale()) + " или «н»</span>") : ("<span class=\"g " + (zone(v)) + "\" style=\"font-size: 18px; height: 40px; line-height: 40px; min-width: 56px\">" + (score(v)) + "</span>")) + "</div>\n" + (v !== undefined && v !== 'Н' ? fragment("journal", "cm", [esc(j.comments[k] || '')]) : '') + "\n" + (v !== undefined ? ("<button class=\"btn ghost sm\" type=\"button\" id=\"del-g\" style=\"color: var(--danger); align-self: flex-start\">Стереть оценку</button>") : ''));
        const cm = panel.querySelector('#cm');
        if (cm) cm.addEventListener('change', () => { if (cm.value.trim()) j.comments[k] = cm.value.trim(); else delete j.comments[k]; if (!save()) return; refresh(r, c); toast('Комментарий сохранён'); });
        const dg = panel.querySelector('#del-g');
        if (dg) dg.addEventListener('click', () => { setValue(r, c, ''); cell(r, c).focus(); });
      };

      const select = (r, c, focus = true) => {
        table.querySelectorAll('.cell.sel').forEach((x) => { x.classList.remove('sel'); x.tabIndex = -1; });
        const td = cell(r, c);
        if (!td) return;
        td.classList.add('sel'); td.tabIndex = 0;
        if (focus) td.focus();
        ui.sel = { r, c };
        cellPanel(r, c);
      };
      const refresh = (r, c) => {
        cell(r, c).innerHTML = cellHtml(r, c);
        table.querySelector(`[data-avg="${r}"]`).innerHTML = avgHtml(r);
      };
      const draftId = (r, c) => JSON.stringify([studentKeys[r], c === C ? 'final' : j.lessons[c].date]);
      const validValue = (c, raw) => {
        const s = String(raw).trim().toLowerCase();
        return s === '' || (c !== C && ['н', 'n', 'y'].includes(s)) || (/^\d{1,3}$/.test(s) && Number(s) >= gradeMin() && Number(s) <= gradeScale());
      };
      let valueError = '';
      const setValue = (r, c, raw, comment) => {
        valueError = '';
        const before = { grades: { ...j.grades }, finals: { ...j.finals }, comments: { ...j.comments } };
        const s = String(raw).trim().toLowerCase();
        if (c === C) {
          if (s === '') delete j.finals[r];
          else if (/^\d{1,3}$/.test(s) && Number(s) >= gradeMin() && Number(s) <= gradeScale()) j.finals[r] = storedScore(Number(s));
          else { valueError = 'Итоговая — число от ' + gradeMin() + ' до ' + gradeScale(); toast(valueError); return false; }
        } else {
          const k = r + '|' + j.lessons[c].date;
          if (s === '') { delete j.grades[k]; delete j.comments[k]; }
          else if (s === 'н' || s === 'n' || s === 'y') { j.grades[k] = 'Н'; delete j.comments[k]; }
          else if (/^\d{1,3}$/.test(s) && Number(s) >= gradeMin() && Number(s) <= gradeScale()) {
            j.grades[k] = storedScore(Number(s));
            if (comment !== undefined) { if (comment) j.comments[k] = comment; else delete j.comments[k]; }
          }
          else { valueError = 'Введите число от ' + gradeMin() + ' до ' + gradeScale() + ' или «н»'; toast(valueError); return false; }
        }
        if (!save()) { j.grades = before.grades; j.finals = before.finals; j.comments = before.comments; valueError = store.SAVE_ERROR; return false; }
        errorDrafts.delete(draftId(r, c));
        refresh(r, c); cellPanel(r, c);
        return true;
      };
      const move = (r, c, dr, dc) => {
        const nr = Math.min(Math.max(r + dr, 0), j.names.length - 1);
        const nc = Math.min(Math.max(c + dc, 0), C);
        select(nr, nc);
      };
      const startEdit = (r, c, initial, focus = true) => {
        const td = cell(r, c);
        const cur = c === C ? j.finals[r] : j.grades[r + '|' + j.lessons[c].date];
        td.innerHTML = ("<input class=\"cell-in\" inputmode=\"" + (c === C ? 'numeric' : 'text') + "\" maxlength=\"3\" aria-label=\"Оценка: " + (esc(j.names[r])) + "\" value=\"" + (esc(initial ?? errorDrafts.get(draftId(r, c))?.value ?? (cur == null ? '' : score(cur)))) + "\">");
        const inp = td.querySelector('input');
        if (focus) { editing = { r, c }; inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); }
        inp.addEventListener('focus', () => { editing = { r, c }; });
        const showError = () => {
          inp.setAttribute('aria-invalid', 'true');
          inp.setAttribute('aria-describedby', 'cell-error-' + r + '-' + c);
          let error = td.querySelector('.cell-error');
          if (!error) { error = document.createElement('span'); error.className = 'cell-error'; error.id = 'cell-error-' + r + '-' + c; error.setAttribute('role', 'alert'); td.appendChild(error); }
          error.textContent = valueError;
          errorDrafts.set(draftId(r, c), { value: inp.value, error: valueError });
        };
        const clearError = () => { inp.removeAttribute('aria-invalid'); inp.removeAttribute('aria-describedby'); td.querySelector('.cell-error')?.remove(); errorDrafts.delete(draftId(r, c)); };
        const savedDraft = errorDrafts.get(draftId(r, c));
        if (savedDraft) { valueError = savedDraft.error; showError(); }
        inp.addEventListener('input', () => {
          if (!errorDrafts.has(draftId(r, c))) return;
          if (validValue(c, inp.value)) clearError();
          else errorDrafts.get(draftId(r, c)).value = inp.value;
        });
        const finish = (dr, dc) => {
          // Сначала снимаем editing: перерисовка вызовет blur, и он не должен сохранить второй раз.
          editing = null;
          const value = inp.value;
          if (!setValue(r, c, value)) { if (document.activeElement === inp) editing = { r, c }; showError(); return; }
          if (dr || dc) move(r, c, dr, dc); else cell(r, c).focus();
        };
        inp.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') { e.preventDefault(); finish(1, 0); }
          else if (e.key === 'Tab') { e.preventDefault(); finish(0, e.shiftKey ? -1 : 1); }
          else if (e.key === 'Escape') { e.preventDefault(); editing = null; errorDrafts.delete(draftId(r, c)); refresh(r, c); cell(r, c).focus(); }
          else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') { e.preventDefault(); finish(e.key === 'ArrowUp' ? -1 : 1, 0); }
        });
        // При ошибке оставляем ввод до исправления или отмены.
        inp.addEventListener('blur', () => {
          if (!editing || editing.r !== r || editing.c !== c) return;
          editing = null;
          if (!setValue(r, c, inp.value)) showError();
        });
      };

      const mobileEdit = (r, c) => {
        mobileCell = { r, c };
        const key = r + '|' + j.lessons[c]?.date, value = c === C ? j.finals[r] : j.grades[key];
        sheet(fragment("journal", "mobile-grade", [esc(j.names[r]), c === C ? 'Итоговая оценка' : ddmm(j.lessons[c].date), gradeMin(), gradeScale(), value == null ? '' : esc(score(value)), c === C ? '' : ("<label class=\"label\" for=\"grade-comment\">Комментарий</label><textarea class=\"input\" id=\"grade-comment\">" + (esc(j.comments[key] || '')) + "</textarea>"), c === C ? '' : ("<button class=\"btn ghost\" type=\"button\" id=\"grade-absent\">Н · пропуск</button>")]), (el, close) => {
          const apply = (raw) => {
            const comment = c === C ? undefined : el.querySelector('#grade-comment').value.trim();
            if (!setValue(r, c, raw, comment)) return;
            close(); toast('Сохранено');
          };
          el.querySelector('form').addEventListener('submit', (e) => { e.preventDefault(); apply(el.querySelector('#grade-value').value); });
          el.querySelector('#grade-absent')?.addEventListener('click', () => apply('Н'));
          el.querySelector('#grade-clear').addEventListener('click', () => apply(''));
        });
      };
      table.addEventListener('click', (e) => {
        const td = e.target.closest('.cell');
        if (td && !e.target.closest('input')) { const r = Number(td.dataset.r), c = Number(td.dataset.c); select(r, c, false); if (window.matchMedia('(max-width: 1023px), (pointer: coarse)').matches) mobileEdit(r, c); else select(r, c); }
      });
      table.addEventListener('dblclick', (e) => {
        const td = e.target.closest('.cell');
        if (td && !window.matchMedia('(max-width: 1023px), (pointer: coarse)').matches) startEdit(Number(td.dataset.r), Number(td.dataset.c));
      });
      table.addEventListener('keydown', (e) => {
        if (editing || !ui.sel || e.target.tagName === 'INPUT' || e.target.tagName === 'BUTTON') return;
        const { r, c } = ui.sel;
        const arrows = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] };
        if (arrows[e.key]) { e.preventDefault(); move(r, c, ...arrows[e.key]); }
        else if (e.key === 'Enter' || e.key === 'F2') { e.preventDefault(); startEdit(r, c); }
        else if (e.key === 'Tab') { e.preventDefault(); move(r, c, 0, e.shiftKey ? -1 : 1); }
        else if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); setValue(r, c, ''); }
        else if (/^[0-9нНnN]$/.test(e.key)) { e.preventDefault(); startEdit(r, c, e.key); }
      });

      root.querySelectorAll('[data-lesson]').forEach((b) => b.addEventListener('click', () => { ui.lesson = Number(b.dataset.lesson); ui.sel = null; if (requestedDate) go('journal/' + ui.key); else rerender(); }));
      root.querySelector('#jk').addEventListener('change', (e) => { ui.key = e.target.value; ui.lesson = null; ui.sel = null; go('journal/' + ui.key); });
      root.querySelector('#types').addEventListener('click', () => workTypesSheet(rerender, j, ui.lesson));
      root.querySelector('#analyze').addEventListener('click', () => {
        const withGrades = j.lessons.map((l, i) => i).filter((i) => j.names.some((_, r) => typeof j.grades[r + '|' + j.lessons[i].date] === 'number'));
        const c = withGrades.includes(ui.lesson) ? ui.lesson : withGrades[withGrades.length - 1];
        if (c == null) { toast('Для анализа сначала поставьте оценки'); return; }
        go('journal/analysis/' + ui.key + '/' + j.lessons[c].date);
      });
      root.querySelector('#avg').addEventListener('click', () => confirmSheet(
        'Выставить средние в «Итог»?', 'Колонка заполнится по среднему каждого ученика (с весами). Уже выставленные итоговые заменятся. Потом любую можно поправить.', 'Заполнить', () => {
          j.names.forEach((_, r) => { const a = store.rowAvg(j, r); if (a != null) j.finals[r] = storedScore(score(a)); });
          if (!save()) return; rerender(); toast('Итоговые заполнены — проверьте и поправьте при необходимости');
        }));
      root.querySelector('#add').addEventListener('click', () => addLessonSheet(j, types, () => { if (!save()) return false; ui.sel = null; rerender(); return true; }));
      root.querySelector('#finals').addEventListener('click', () => finalsSheet(j, () => { if (!save()) return false; rerender(); return true; }));

      j.names.forEach((name, r) => {
        [...j.lessons.map((l) => l.date), 'final'].forEach((date, c) => {
          const draft = errorDrafts.get(draftId(r, c));
          if (draft) startEdit(r, c, draft.value, false);
        });
      });
      if (ui.sel && cell(ui.sel.r, ui.sel.c)) select(ui.sel.r, ui.sel.c, false); else lessonPanel();
    }
  };
}

function finalsSheet(j, done) {
  sheet(fragment("journal", "fill", [periodLabel(), icon('close', 20), gradeMin(), gradeScale(), j.names.map((n, r) => {
    const a = store.rowAvg(j, r);
    return fragment("journal", "fin-row", [esc(n), a == null ? ("<span class=\"muted small\">нет оценок</span>") : ("<span class=\"g avg " + (zone(a)) + "\">" + (fmt(a)) + "</span>"), r, typeof j.finals[r] === 'number' ? score(j.finals[r]) : '', esc(n)]);
  }).join('')]), (el, close) => {
    const inputs = [...el.querySelectorAll('.fin-in')];
    el.querySelector('#fill').addEventListener('click', () => inputs.forEach((i) => { const a = store.rowAvg(j, Number(i.dataset.r)); if (a != null) i.value = score(a); }));
    el.querySelector('#fin-f').addEventListener('submit', (e) => {
      e.preventDefault();
      const bad = inputs.find((i) => i.value.trim() && !(/^\d{1,3}$/.test(i.value.trim()) && Number(i.value) >= gradeMin() && Number(i.value) <= gradeScale()));
      inputs.forEach((i) => i.removeAttribute('aria-invalid'));
      if (bad) {
        const err = el.querySelector('#fin-e');
        err.textContent = 'Итоговая — число от ' + gradeMin() + ' до ' + gradeScale() + '.'; err.hidden = false;
        bad.setAttribute('aria-invalid', 'true'); bad.focus(); return;
      }
      inputs.forEach((i) => { const r = Number(i.dataset.r); if (i.value.trim()) j.finals[r] = storedScore(Number(i.value)); else delete j.finals[r]; });
      const n = inputs.filter((i) => i.value.trim()).length;
      if (done() === false) return; close();
      toast('Итоговые сохранены: ' + n + ' из ' + inputs.length);
    });
  });
}

function confirmSheet(title, text, okLabel, onOk) {
  sheet(fragment("journal", "ok", [esc(title), esc(text), esc(okLabel)]), (el, close) => el.querySelector('#ok').addEventListener('click', () => { close(); onOk(); }));
}

function newTypeSheet(onCreated) {
  sheet(fragment("journal", "nt", [[1, 2, 3].map((w) => ("<button type=\"button\" data-w=\"" + (w) + "\" aria-pressed=\"" + (w === 2) + "\">×" + (w) + "</button>")).join('')]), (el, close) => {
    let w = 2;
    el.querySelectorAll('[data-w]').forEach((b) => b.addEventListener('click', () => {
      w = Number(b.dataset.w);
      el.querySelectorAll('[data-w]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    }));
    el.querySelector('#nt').addEventListener('submit', (e) => {
      e.preventDefault();
      const name = el.querySelector('#ntn').value.trim();
      const err = el.querySelector('#nte');
      const list = store.workTypes();
      if (!name) { err.textContent = 'Введите название.'; err.hidden = false; return; }
      if (list.some((t) => t.name.toLowerCase() === name.toLowerCase())) { err.textContent = 'Такой тип уже есть — выберите его в списке.'; err.hidden = false; return; }
      const id = 'u' + Date.now();
      list.push({ id, name, w });
      if (!store.setWorkTypes(list)) return toast(store.SAVE_ERROR);
      close(); toast('Тип «' + name + ' ×' + w + '» создан'); onCreated(id);
    });
  });
}

function addLessonSheet(j, types, done) {
  const today = iso(store.NOW);
  sheet(fragment("journal", "nl", [today, store.currentPeriod().from || '', periodEnd(), types.map((t) => ("<option value=\"" + (t.id) + "\">" + (esc(t.name)) + " ×" + (t.w) + "</option>")).join('')]), (el, close) => {
    el.querySelector('#nl').addEventListener('submit', (e) => {
      e.preventDefault();
      const d = el.querySelector('#nd').value;
      const err = el.querySelector('#ne');
      const from = store.currentPeriod().from;
      const to = store.currentPeriod().to;
      if (!d || (from && d < from) || (to && d > to)) { err.textContent = 'Выберите дату внутри текущего учебного периода.'; err.hidden = false; return; }
      if (j.lessons.some((l) => l.date === d)) { err.textContent = 'Урок с этой датой уже есть.'; err.hidden = false; return; }
      j.lessons.push({ date: d, type: el.querySelector('#ntp').value, hw: '' });
      j.lessons.sort((a, b) => a.date.localeCompare(b.date));
      ui.lesson = j.lessons.findIndex((l) => l.date === d);
      if (done() === false) return; close(); toast('Урок добавлен');
    });
  });
}

function workTypesSheet(rerender, j, lessonIdx) {
  let list = store.workTypes();
  const lesson = j.lessons[lessonIdx];
  const draw = (el) => {
    el.querySelector('#wt-list').innerHTML = list.map((t, i) => fragment("journal", "wt-row", [lesson && lesson.type === t.id ? ' on' : '', t.id, lesson ? '' : 'disabled', !!lesson && lesson.type === t.id, esc(t.name), esc(t.name), [1, 2, 3].map((w) => ("<button type=\"button\" data-i=\"" + (i) + "\" data-w=\"" + (w) + "\" aria-pressed=\"" + (t.w === w) + "\">×" + (w) + "</button>")).join(''), i, esc(t.name), t.id === 'lesson' ? 'disabled' : '', icon('trash', 18)])).join('');
    el.querySelectorAll('[data-pick]').forEach((b) => b.addEventListener('click', () => {
      lesson.type = b.dataset.pick; if (!store.saveJournal(j)) return toast(store.SAVE_ERROR); draw(el);
      toast('Урок ' + ddmm(lesson.date) + ' — «' + store.typeOf(lesson.type).name + '»');
    }));
    el.querySelectorAll('[data-w]').forEach((b) => b.addEventListener('click', () => { list[b.dataset.i].w = Number(b.dataset.w); if (!store.setWorkTypes(list)) return toast(store.SAVE_ERROR); draw(el); }));
    el.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', () => {
      const t = list[b.dataset.del];
      if (t.id === 'lesson') { toast('«Работу на уроке» удалить нельзя — это тип по умолчанию'); return; }
      const count = store.workTypeUsage(t.id);
      confirmSheet('Удалить тип «' + t.name + '»?', count
        ? 'Уроков с этим типом: ' + count + '. Во всех периодах они станут «Работой на уроке» с весом ×' + store.typeOf('lesson').w + '. Средние изменятся. Оценки и комментарии сохранятся.'
        : 'Этот тип пока не используется в журналах.', 'Удалить', () => {
          if (!store.removeWorkType(t.id)) return toast(store.SAVE_ERROR); list = store.workTypes(); draw(el); rerender(); toast('Тип «' + t.name + '» удалён');
        });
    }));
  };
  sheet(fragment("journal", "wt-list", [icon('close', 20), lesson ? ("Нажмите на тип — он встанет уроку <b>" + (ddmm(lesson.date)) + "</b>.") : 'Сначала добавьте урок кнопкой «+».']), (el) => {
    draw(el);
    el.querySelector('#wt-add').addEventListener('submit', (e) => {
      e.preventDefault();
      const name = el.querySelector('#wt-name').value.trim();
      if (!name) { el.querySelector('#wt-name').focus(); return; }
      if (list.some((t) => t.name.toLowerCase() === name.toLowerCase())) { toast('Такой тип уже есть'); return; }
      list.push({ id: 'u' + Date.now(), name, w: Number(el.querySelector('#wt-w').value) });
      if (!store.setWorkTypes(list)) return toast(store.SAVE_ERROR); el.querySelector('#wt-name').value = ''; draw(el); toast('Тип добавлен');
    });
    el.closest('.overlay').addEventListener('click', (e) => { if (e.target.closest('[data-close]') || e.target.classList.contains('overlay')) setTimeout(rerender, 0); });
  });
}
export { journalView };
