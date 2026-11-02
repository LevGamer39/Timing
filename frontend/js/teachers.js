import { fragment } from './templates.js';
import * as store from './store.js';
import { esc, icon, toast, sheet } from "./ui.js";
import { header, confirmSheet } from "./common.js";
import { copyCode } from './clipboard.js';

import { subjName, accessHtml, bindInvites } from "./shared/admin.js";

function teachersView({ rerender }) {
  const server = store.isServer();
  const list = store.teachers();
  const cls = store.classes();
  const classTeacher = (id) => Object.values(cls).filter((c) => c.teacher === id).map((c) => c.name).join(', ');
  const clsName = (k) => (cls[k] ? cls[k].name : k);

  const subjectSheet = (t) => sheet(fragment("teachers", "sub", [esc(t.name), Object.keys(store.SUBJECTS).map((k) => ("<option value=\"" + (k) + "\"" + ((t.subject || '') === k ? ' selected' : '') + ">" + (esc(subjName(k))) + (/4$/.test(k) ? ' (нач. школа)' : '') + "</option>")).join('')]), (el, close) => el.querySelector('#sf').addEventListener('submit', (e) => {
    e.preventDefault();
    const value = el.querySelector('#ss').value;
    store.apiSetTeacherSubject(t.id, value).then((result) => {
      if (typeof result === 'string') { el.querySelector('#se').textContent = result; el.querySelector('#se').hidden = false; return; }
      close(); rerender(); toast('Предмет сохранён');
    });
  }));

  const loadSheet = (t) => {
    if (server) {
      sheet(fragment("teachers", "ld-server", [esc(t.name), icon('close', 18), esc(t.subject ? subjName(t.subject) : 'предмет не указан'), t.classCount || 0]), (el) => {
        el.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => { el.closest('.overlay')?.remove(); rerender(); }));
      });
      return;
    }
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

  const person = (t, i) => {
    if (server) {
      return fragment("teachers", "person-server", [
        esc(t.name),
        t.subject ? '<span class="hint">' + esc(subjName(t.subject)) + '</span>' : '<span class="hint">Предмет не указан</span>',
        (t.classCount ? '<span class="pill">Классов: ' + t.classCount + '</span>' : '') + (classTeacher(t.id) ? '<span class="pill">Классное руководство · ' + esc(classTeacher(t.id)) + '</span>' : ''),
        '', i, esc(t.name)
      ]);
    }
    return fragment("teachers", "person", [esc(t.name), classTeacher(t.id) ? '<span class="hint">Классный руководитель · ' + esc(classTeacher(t.id)) + '</span>' : '', t.load.map(([s, ks]) => '<span class="pill">' + esc(subjName(s)) + ' · ' + ks.map(clsName).join(', ') + '</span>').join('') || '<span class="hint">Нет нагрузки</span>', accessHtml(store.teacherLogin(t)), i]);
  };

  return {
    html: fragment("teachers", "add", [header(), icon('plus', 18), list.map(person).join('') || '<div class="empty-day">Учителей пока нет</div>']) + (server ? fragment("teachers", "codes", []) : ''),
    mount(root) {
      if (!server) {
        root.querySelectorAll('[data-load]').forEach((b) => b.addEventListener('click', () => loadSheet(list[b.dataset.load])));
        bindInvites(root, rerender);
      } else {
        root.querySelectorAll('[data-subject]').forEach((b) => b.addEventListener('click', () => subjectSheet(list[b.dataset.subject])));
        const codesBox = root.querySelector('#codes-list');
        if (codesBox) {
          const drawCodes = (items) => {
            codesBox.innerHTML = items.length ? items.map((c) => (
              '<div class="list-row" style="padding: 8px 0">'
              + '<span class="grow"><b class="mono">' + esc(c.code) + '</b><br><span class="muted small">' + (c.is_used ? 'Использован' : 'Свободен') + '</span></span>'
              + (c.is_used ? '' : copyCode(c.code, 'код учителя') + '<button class="icon-btn" type="button" data-del-code="' + c.id + '" aria-label="Удалить код">' + icon('trash', 18) + '</button>')
              + '</div>'
            )).join('') : '<p class="muted">Свободных кодов нет. Нажмите «Добавить учителя», чтобы выдать код.</p>';
            codesBox.querySelectorAll('[data-del-code]').forEach((b) => b.addEventListener('click', () => {
              store.apiDeleteInvite(b.dataset.delCode).then((result) => {
                if (typeof result === 'string') return toast(result);
                toast('Код удалён'); rerender();
              });
            }));
          };
          store.apiInviteCodes().then((items) => drawCodes(Array.isArray(items) ? items : []));
        }
      }
      root.querySelectorAll('[data-del-teacher]').forEach((button) => button.addEventListener('click', () => {
        const teacher = list[Number(button.dataset.delTeacher)];
        confirmSheet('Удалить учителя?', teacher.name + '. Его вход и приглашение отключатся, классное руководство и назначения снимутся. Оценки и журналы сохранятся.', 'Удалить', () => {
          if (server) {
            store.apiDeactivateTeacher(teacher.id).then((result) => {
              if (typeof result === 'string') return toast(result);
              rerender(); toast('Учитель деактивирован');
            });
            return;
          }
          const error = store.deleteTeacher(teacher.id);
          if (error) return toast(error);
          rerender(); toast('Учитель удалён');
        }, true);
      }));
      root.querySelector('#add').addEventListener('click', () => {
        if (server) {
          store.apiTeacherInvite().then((result) => {
            if (!result || typeof result === 'string') return toast(typeof result === 'string' ? result : store.SAVE_ERROR);
            sheet(fragment("teachers", "inv", [esc(result.code), copyCode(result.code, 'код учителя')]), (el) => {
              el.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => { el.closest('.overlay')?.remove(); rerender(); }));
            });
          });
          return;
        }
        sheet(fragment("teachers", "tf", []), (el, close) => el.querySelector('#tf').addEventListener('submit', (e) => {
          e.preventDefault();
          const n = el.querySelector('#tn').value.trim().replace(/\s+/g, ' ');
          const er = el.querySelector('#te');
          if (n.split(' ').length < 2) { er.textContent = 'Введите фамилию и имя.'; er.hidden = false; return; }
          const result = store.addTeacher(n);
          if (result.error) { er.textContent = result.error; er.hidden = false; return; }
          close(); rerender();
          toast('Учитель добавлен. Теперь укажите нагрузку — кнопка «Нагрузка»');
        }));
      });
    }
  };
}
export { teachersView };
