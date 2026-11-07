import { fragment } from './templates.js';
import * as store from './store.js';
import { esc, icon, toast, sheet, plural } from "./ui.js";
import { header, confirmSheet } from './common.js';

import { accessHtml, bindInvites, subjName } from "./shared/admin.js";
import { showClassInvitations } from './invite-print.js';
import { copyCode } from './clipboard.js';

function classesView({ params, go, rerender }) {
  const all = store.classes(), admin = store.role() === 'admin', own = store.homeroom();
  const server = store.isServer();
  const allowed = admin ? Object.values(all) : own ? [own] : [];

  const addClass = (el, close) => {
    const name = el.querySelector('#cn').value.trim().toUpperCase();
    const er = el.querySelector('#ce');
    if (!/^\d{1,2}[А-ЯЁ]$/.test(name)) { er.textContent = 'Введите номер и букву, например 9А.'; er.hidden = false; return; }
    if (Object.values(all).some((x) => x.name === name)) { er.textContent = 'Такой класс уже есть.'; er.hidden = false; return; }
    const teacherId = el.querySelector('#cftv').value;
    if (!teacherId) { er.textContent = store.teachers().length ? 'Выберите классного руководителя.' : 'Сначала добавьте хотя бы одного учителя.'; er.hidden = false; return; }
    if (!server) {
      const k = name.toLowerCase().replace(/[а-яё]/, (ch) => ({ а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e' })[ch] || 'x' + ch.charCodeAt(0));
      all[k] = { key: k, name, teacher: teacherId, students: [] };
      if (!store.saveClasses(all)) return toast(store.SAVE_ERROR);
      close(); toast('Класс ' + name + ' добавлен'); go('classes/' + k);
      return;
    }
    store.apiCreateClass(name, teacherId).then((result) => {
      if (typeof result === 'string') { er.textContent = result; er.hidden = false; return; }
      close(); rerender(); toast('Класс ' + name + ' добавлен');
    });
  };

  const bindAddClass = (root) => {
    root.querySelector('#add-class')?.addEventListener('click', () => sheet(fragment("classes", "cf", []), (el, close) => {
      const input = el.querySelector('#cft'), value = el.querySelector('#cftv'), list = el.querySelector('#cfts'), picker = el.querySelector('#cftp'), teachers = store.teachers();
      const show = (open) => {
        list.hidden = !open;
        picker.classList.toggle('open', open);
        input.setAttribute('aria-expanded', String(open));
      };
      const draw = (query) => {
        const needle = query.trim().toLowerCase();
        const hits = teachers.filter((t) => !needle || t.name.toLowerCase().includes(needle));
        show(!!hits.length);
        list.innerHTML = hits.map((t) => {
          const subs = (t.subjects && t.subjects.length ? t.subjects : (t.subject ? [t.subject] : [])).map(subjName).join(', ');
          return '<button type="button" role="option" data-id="' + esc(t.id) + '"><b>' + esc(t.name) + '</b>' + (subs ? '<span class="muted small">' + esc(subs) + '</span>' : '') + '</button>';
        }).join('');
        list.querySelectorAll('button').forEach((b) => b.addEventListener('click', () => {
          const t = teachers.find((x) => x.id === b.dataset.id);
          value.value = t.id; input.value = t.name;
          show(false);
        }));
      };
      list.addEventListener('mousedown', (e) => e.preventDefault());
      input.addEventListener('focus', () => draw(''));
      input.addEventListener('input', () => { value.value = ''; draw(input.value); });
      input.addEventListener('blur', () => show(false));
      el.querySelector('#cf').addEventListener('submit', (e) => { e.preventDefault(); addClass(el, close); });
    }));
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

  const personHtml = (st, i) => server
    ? fragment("classes", "person", [esc(st.name), '<span class="hint">' + (st.status === 'ok' ? 'Аккаунт активен' : 'Ждёт подтверждения почты') + '</span>', st.status === 'ok' ? '' : '<button class="btn ghost sm" type="button" data-activate="' + i + '" style="margin-left: auto">Активировать</button>', i, esc(st.name), icon('trash', 18), '№' + esc(st.id)])
    : fragment("classes", "person", [esc(st.name), accessHtml(st.login), parentHtml(st, i), i, esc(st.name), icon('trash', 18), '<button class="parent-link" type="button" data-parent="' + i + '">+ родитель</button>']);

  const inviteCard = server && c.invite
    ? fragment("classes", "invite", [esc(c.invite), copyCode(c.invite, 'код класса')])
    : '';

  const controls = admin
    ? fragment("classes", "add-class", [icon('plus', 18), esc(c.name), teachers.map((t) => ("<option value=\"" + (t.id) + "\" " + (t.id === c.teacher ? 'selected' : '') + ">" + (esc(t.name)) + "</option>")).join('')]) + (server ? '<button class="btn ghost sm" id="del-class" type="button" style="color: var(--danger)">Удалить класс</button>' : '')
    : '';

  return {
    html: fragment("classes", "add", [header(), admin ? 'Классы и ученики' : 'Ученики и приглашения', icon('plus', 18), esc(c.name), allowed.map((x) => ("<a href=\"#/classes/" + (x.key) + "\"" + (x.key === key ? ' aria-current="true"' : '') + ">" + (esc(x.name)) + "</a>")).join(''), controls, esc(c.name), c.students.length, plural(c.students.length, 'ученик', 'ученика', 'учеников'), c.students.map(personHtml).join('') || '<div class="empty-day">Учеников пока нет</div>', inviteCard, admin && server ? fragment("classes", "mig-actions") : '']),
    mount(root) {
      const save = () => { all[key] = c; const ok = store.saveClasses(all); if (!ok) toast(store.SAVE_ERROR); return ok; };
      if (!server) bindInvites(root, rerender);
      root.querySelector('#ct')?.addEventListener('change', (e) => {
        if (!server) { c.teacher = e.target.value; if (save()) toast('Классный руководитель сохранён'); return; }
        if (!e.target.value) { toast('Выберите учителя — класс без руководителя не сохраняется.'); e.target.value = c.teacher; return; }
        store.apiPatchClass(key, { teacher_id: Number(e.target.value) }).then((result) => {
          if (typeof result === 'string') return toast(result);
          rerender(); toast('Классный руководитель сохранён');
        });
      });
      bindAddClass(root);

      const migFile = root.querySelector('#mig-file');
      root.querySelector('#mig-export')?.addEventListener('click', () => {
        store.apiExportStudents().then((doc) => {
          if (!doc || !Array.isArray(doc.students)) return toast('Сервер не вернул список учеников');
          const link = document.createElement('a');
          link.href = URL.createObjectURL(new Blob([JSON.stringify(doc, null, 2)], { type: 'application/json' }));
          link.download = 'ученики.json';
          link.click();
          URL.revokeObjectURL(link.href);
          toast('Экспортировано учеников: ' + doc.students.length);
        }).catch(() => toast('Не удалось выгрузить учеников'));
      });
      root.querySelector('#mig-import')?.addEventListener('click', () => migFile?.click());
      migFile?.addEventListener('change', () => {
        const file = migFile.files && migFile.files[0];
        if (!file) return;
        migFile.value = '';
        const reader = new FileReader();
        reader.onload = () => {
          let doc;
          try { doc = JSON.parse(String(reader.result)); } catch { return toast('Это не JSON-файл'); }
          store.apiImportStudents(doc).then((result) => {
            if (typeof result === 'string') return toast(result);
            toast('Импорт: добавлено ' + result.created + ', обновлено ' + result.updated + ', оценок ' + result.grades_added);
            if (result.unknown_classes?.length) toast('Классы не найдены: ' + result.unknown_classes.join(', ') + '. Ученики перенесены без класса.');
            if (result.warnings?.length) toast(result.warnings[0] + (result.warnings.length > 1 ? ' (+' + (result.warnings.length - 1) + ')' : ''));
            rerender();
          });
        };
        reader.readAsText(file);
      });

      root.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', () => {
        const st = c.students[b.dataset.del];
        confirmSheet('Удалить ученика?', st.name + ' пропадёт из класса и журналов.', 'Удалить', () => {
          if (!server) {
            c.students.splice(Number(b.dataset.del), 1); save(); rerender();
            return;
          }
          store.apiRemoveStudent(key, st.id).then((result) => {
            if (typeof result === 'string') return toast(result);
            rerender(); toast('Ученик убран из класса');
          });
        }, true);
      }));
      root.querySelectorAll('[data-activate]').forEach((b) => b.addEventListener('click', () => {
        const st = c.students[b.dataset.activate];
        store.apiActivateUser(st.id, true).then((result) => {
          if (typeof result === 'string') return toast(result);
          rerender(); toast('Аккаунт активирован');
        });
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

      root.querySelector('#add').addEventListener('click', () => {
        if (server) return addStudentSheet();
        sheet(fragment("classes", "sf", [esc(c.name)]), (el, close) => el.querySelector('form').addEventListener('submit', (e) => {
          e.preventDefault(); const name = el.querySelector('#sn').value.trim().replace(/\s+/g, ' ');
          if (!name || !/[а-яёa-z]/i.test(name)) { el.querySelector('#se').textContent = 'Введите ФИО ученика.'; el.querySelector('#se').hidden = false; return; }
          const login = store.uniqueLogin(name); c.students.push({ name, login, status: 'wait', parent: '' }); c.students.sort((a, b) => a.name.localeCompare(b.name, 'ru'));
          if (!save() || !store.invitation(login)) return toast(store.SAVE_ERROR); close(); rerender(); toast('Приглашение готово');
        }));
      });

      const addStudentSheet = () => {
        const options = store.unassignedStudents(key);
        if (!options.length) return toast('Нет учеников вне класса. Ученик появляется после регистрации в боте по коду класса.');
        sheet(fragment("classes", "af", [options.map((s) => '<option value="' + esc(s.id) + '">' + esc(s.name) + (s.cls ? ' · ' + esc(s.cls) : '') + '</option>').join('')]), (el, close) => el.querySelector('#af').addEventListener('submit', (e) => {
          e.preventDefault();
          const id = el.querySelector('#as').value;
          store.apiAddStudent(key, id).then((result) => {
            if (typeof result === 'string') { el.querySelector('#ae').textContent = result; el.querySelector('#ae').hidden = false; return; }
            close(); rerender(); toast('Ученик добавлен в класс');
          });
        }));
      };

      root.querySelector('#print')?.addEventListener('click', () => {
        if (server) return c.invite ? copyCode(c.invite, 'код класса') : toast('У класса нет кода.');
        showClassInvitations(key);
      });
      root.querySelector('#del-class')?.addEventListener('click', () => confirmSheet('Удалить класс?', c.name + ' будет удалён. Ученики и оценки сохранятся, но класс из журналов пропадёт.', 'Удалить', () => {
        store.apiDeleteClass(key).then((result) => {
          if (typeof result === 'string') return toast(result);
          toast('Класс удалён'); go('classes');
        });
      }, true));
    }
  };
}
export { classesView };
