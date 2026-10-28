import { fragment } from './templates.js';
import * as store from './store.js';
import { esc, icon, toast, sheet, plural } from "./ui.js";
import { header, confirmSheet } from './common.js';

import { accessHtml, bindInvites } from "./shared/admin.js";
import { showClassInvitations } from './invite-print.js';

function classesView({ params, go, rerender }) {
  const all = store.classes(), admin = store.role() === 'admin', own = store.homeroom();
  const allowed = admin ? Object.values(all) : own ? [own] : [];
  const bindAddClass = (root) => {
    root.querySelector('#add-class')?.addEventListener('click', () => sheet(fragment("classes", "cf", []), (el, close) => el.querySelector('#cf').addEventListener('submit', (e) => {
        e.preventDefault();
        const n = el.querySelector('#cn').value.trim().toUpperCase();
        const er = el.querySelector('#ce');
        if (!/^\d{1,2}[А-ЯЁ]$/.test(n)) { er.textContent = 'Введите номер и букву, например 9А.'; er.hidden = false; return; }
        if (Object.values(all).some((x) => x.name === n)) { er.textContent = 'Такой класс уже есть.'; er.hidden = false; return; }
        const k = n.toLowerCase().replace(/[а-яё]/, (ch) => ({ а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e' })[ch] || 'x' + ch.charCodeAt(0));
        all[k] = { key: k, name: n, teacher: '', students: [] };
        if (!store.saveClasses(all)) return toast(store.SAVE_ERROR); close(); toast('Класс ' + n + ' добавлен'); go('classes/' + k);
      })));
  };
  const c = allowed.find((x) => x.key === params[0]) || allowed[0];
  if (!c) return {
    html: header() + (admin
      ? '<h1 class="title-l">Классы и ученики</h1><div class="card pad empty-state"><p>Классов пока нет</p><button class="text-action" id="add-class" type="button">Добавить класс</button></div>'
      : '<h1 class="title-l">Ученики и приглашения</h1><div class="card pad muted">Классное руководство не назначено</div>'),
    mount: bindAddClass
  };
  const key = c.key, teachers = store.teachers();
  const parentHtml = (s) => store.parentsForStudent(s).map((parent) => fragment('classes', 'parent', [esc(store.accountByLogin(parent).name), accessHtml(parent), esc(parent), esc(s.login), icon('trash', 18)])).join('');
  return {
    html: fragment("classes", "add", [header(), admin ? 'Классы и ученики' : 'Ученики и приглашения', icon('plus', 18), esc(c.name), allowed.map((x) => ("<a href=\"#/classes/" + (x.key) + "\"" + (x.key === key ? ' aria-current="true"' : '') + ">" + (esc(x.name)) + "</a>")).join(''), admin ? fragment("classes", "add-class", [icon('plus', 18), esc(c.name), teachers.map((t) => ("<option value=\"" + (t.id) + "\" " + (t.id === c.teacher ? 'selected' : '') + ">" + (esc(t.name)) + "</option>")).join('')]) : '', esc(c.name), c.students.length, plural(c.students.length, 'ученик', 'ученика', 'учеников'), c.students.map((st, i) => fragment("classes", "person", [esc(st.name), accessHtml(st.login), parentHtml(st, i), i, esc(st.name), icon('trash', 18), '<button class="parent-link" type="button" data-parent="' + i + '">+ родитель</button>'])).join('') || '<div class="empty-day">Учеников пока нет</div>']),
    mount(root) {
      const save = () => { all[key] = c; const ok = store.saveClasses(all); if (!ok) toast(store.SAVE_ERROR); return ok; };
      bindInvites(root, rerender);
      root.querySelector('#ct')?.addEventListener('change', (e) => { c.teacher = e.target.value; if (save()) toast('Классный руководитель сохранён'); });
      bindAddClass(root);

      root.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', () => {
        const st = c.students[b.dataset.del];
        confirmSheet('Удалить ученика?', st.name + ' пропадёт из класса и журналов. Приглашение перестанет действовать.', 'Удалить', () => {
          c.students.splice(Number(b.dataset.del), 1); save(); rerender();
        }, true);
      }));
      root.querySelectorAll('[data-remove-parent]').forEach((b) => b.addEventListener('click', () => {
        const sid = store.studentIdForLogin(b.dataset.student), parent = store.accountByLogin(b.dataset.removeParent);
        if (!parent) return toast('Родитель уже удалён. Обновите список.');
        const st = c.students.find((s) => s.login === b.dataset.student);
        const otherChildren = parent.children.filter((id) => id !== sid).length;
        confirmSheet('Удалить родителя у ребёнка?', parent.name + ' больше не сможет смотреть данные ' + st.name + '. ' + (otherChildren ? 'Доступ к другим детям сохранится.' : 'Приглашение и доступ к сайту перестанут действовать.'), 'Удалить', () => {
          const error = store.removeParentAccess(sid, b.dataset.removeParent);
          if (error) return toast(error);
          rerender(); toast('Родитель удалён из списка ребёнка');
        }, true);
      }));
      root.querySelectorAll('[data-parent]').forEach((b) => b.addEventListener('click', () => {
        const st = c.students[b.dataset.parent];
        sheet(fragment('classes', 'pf', [esc(st.name)]), (el, close) => {
          const list = el.querySelector('#parent-fields');
          let next = 0;
          const addField = () => {
            const id = 'parent-name-' + ++next;
            list.insertAdjacentHTML('beforeend', fragment('classes', 'parent-field', [id, next, icon('trash', 18)]));
            list.querySelectorAll('[data-remove-field]').forEach((button) => { button.hidden = list.children.length === 1; });
            list.lastElementChild.querySelector('input').focus();
          };
          el.querySelector('#add-parent-field').addEventListener('click', addField);
          list.addEventListener('click', (event) => {
            const button = event.target.closest('[data-remove-field]');
            if (!button) return;
            const row = button.closest('.parent-field'), focus = row.previousElementSibling || row.nextElementSibling;
            row.remove();
            list.querySelectorAll('[data-remove-field]').forEach((button) => { button.hidden = list.children.length === 1; });
            focus?.querySelector('input').focus();
          });
          addField();
          el.querySelector('form').addEventListener('submit', (e) => {
            e.preventDefault();
            const inputs = [...list.querySelectorAll('input')], error = el.querySelector('#pe');
            inputs.forEach((input) => input.removeAttribute('aria-invalid'));
            const invalid = inputs.find((input) => input.value.trim().split(/\s+/).length < 2 || !/[а-яёa-z]/i.test(input.value));
            if (invalid) { error.textContent = 'Для каждого родителя введите фамилию и имя.'; error.hidden = false; invalid.setAttribute('aria-invalid', 'true'); invalid.focus(); return; }
            const result = store.createParentsAccess(inputs.map((input) => input.value), store.studentIdForLogin(st.login));
            if (result.error) { error.textContent = result.error; error.hidden = false; return; }
            close(); rerender(); toast(result.parents.length === 1 ? 'Приглашение для родителя готово' : 'Приглашения для родителей готовы');
          });
        });
      }));
      root.querySelector('#add').addEventListener('click', () => sheet(fragment("classes", "sf", [esc(c.name)]), (el, close) => el.querySelector('form').addEventListener('submit', (e) => {
        e.preventDefault(); const name = el.querySelector('#sn').value.trim().replace(/\s+/g, ' ');
        if (!name || !/[а-яёa-z]/i.test(name)) { el.querySelector('#se').textContent = 'Введите ФИО ученика.'; el.querySelector('#se').hidden = false; return; }
        const login = store.uniqueLogin(name); c.students.push({ name, login, status: 'wait', parent: '' }); c.students.sort((a, b) => a.name.localeCompare(b.name, 'ru'));
        if (!save() || !store.invitation(login)) return toast(store.SAVE_ERROR); close(); rerender(); toast('Приглашение готово');
      })));
      root.querySelector('#print').addEventListener('click', () => showClassInvitations(key));
    }
  };
}
export { classesView };
