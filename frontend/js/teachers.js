import { fragment } from './templates.js';
import * as store from './store.js';
import { esc, icon, toast, sheet } from "./ui.js";
import { header, confirmSheet } from "./common.js";

import { subjName, accessHtml, bindInvites } from "./shared/admin.js";

function teachersView({ rerender }) {
  const list = store.teachers();
  const cls = store.classes();
  const classTeacher = (id) => Object.values(cls).filter((c) => c.teacher === id).map((c) => c.name).join(', ');
  const clsName = (k) => (cls[k] ? cls[k].name : k);

  const loadSheet = (t) => {
    const draw = (el) => {
      el.querySelector('#ld').innerHTML = t.load.length ? t.load.map(([s, ks], i) => fragment("teachers", "list-row", [esc(subjName(s)), ks.map(clsName).join(', '), i, esc(subjName(s)), icon('close', 18)])).join('') : ("<p class=\"muted\">Нагрузки пока нет</p>");
      el.querySelectorAll('[data-rm]').forEach((b) => b.addEventListener('click', () => { t.load.splice(Number(b.dataset.rm), 1); if (!store.saveTeachers(list)) return toast(store.SAVE_ERROR); draw(el); }));
    };
    sheet(fragment("teachers", "ld", [esc(t.name), icon('close', 18), Object.keys(store.SUBJECTS).map((k) => ("<option value=\"" + (k) + "\">" + (esc(subjName(k))) + (/4$/.test(k) ? ' (нач. школа)' : '') + "</option>")).join(''), Object.values(cls).map((c) => ("<label class=\"pill\" style=\"min-height: 44px; cursor: pointer\"><input type=\"checkbox\" value=\"" + (c.key) + "\"> " + (esc(c.name)) + "</label>")).join(''), icon('plus', 18)]), (el) => {
      draw(el);
      el.querySelector('#lf').addEventListener('submit', (e) => {
        e.preventDefault();
        const s = el.querySelector('#ls').value;
        const ks = [...el.querySelectorAll('#lf input:checked')].map((i) => i.value);
        if (!ks.length) { toast('Отметьте хотя бы один класс'); return; }
        const ex = t.load.find(([x]) => x === s);
        if (ex) ks.forEach((k) => { if (!ex[1].includes(k)) ex[1].push(k); }); else t.load.push([s, ks]);
        if (!store.saveTeachers(list)) return toast(store.SAVE_ERROR); draw(el); el.querySelectorAll('#lf input:checked').forEach((i) => { i.checked = false; });
      });
      el.closest('.overlay').addEventListener('click', (e) => { if (e.target.closest('[data-close]') || e.target.classList.contains('overlay')) setTimeout(rerender, 0); });
    });
  };

  return {
    html: fragment("teachers", "add", [header(), icon('plus', 18), list.map((t, i) => fragment("teachers", "person", [esc(t.name), classTeacher(t.id) ? '<span class="hint">Классный руководитель · ' + esc(classTeacher(t.id)) + '</span>' : '', t.load.map(([s, ks]) => '<span class="pill">' + esc(subjName(s)) + ' · ' + ks.map(clsName).join(', ') + '</span>').join('') || '<span class="hint">Нет нагрузки</span>', accessHtml(store.teacherLogin(t)), i])).join('') || '<div class="empty-day">Учителей пока нет</div>']),
    mount(root) {
      root.querySelectorAll('[data-load]').forEach((b) => b.addEventListener('click', () => loadSheet(list[b.dataset.load])));
      bindInvites(root, rerender);
      root.querySelectorAll('[data-del-teacher]').forEach((button) => button.addEventListener('click', () => {
        const teacher = list[Number(button.dataset.delTeacher)];
        confirmSheet('Удалить учителя?', teacher.name + '. Его вход и приглашение отключатся, классное руководство и назначения снимутся. Оценки и журналы сохранятся.', 'Удалить', () => {
          const error = store.deleteTeacher(teacher.id);
          if (error) return toast(error);
          rerender(); toast('Учитель удалён');
        }, true);
      }));
      root.querySelector('#add').addEventListener('click', () => sheet(fragment("teachers", "tf", []), (el, close) => el.querySelector('#tf').addEventListener('submit', (e) => {
        e.preventDefault();
        const n = el.querySelector('#tn').value.trim().replace(/\s+/g, ' ');
        const er = el.querySelector('#te');
        if (n.split(' ').length < 2) { er.textContent = 'Введите фамилию и имя.'; er.hidden = false; return; }
        const result = store.addTeacher(n);
        if (result.error) { er.textContent = result.error; er.hidden = false; return; }
        close(); rerender();
        toast('Учитель добавлен. Теперь укажите нагрузку — кнопка «Нагрузка»');
      })));
    }
  };
}
export { teachersView };
