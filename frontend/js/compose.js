import { fragment } from './templates.js';
import * as store from './store.js';
import { esc, icon, toast, sheet } from "./ui.js";
import { createFileList } from './shared/file-upload.js';

function composeView({ params, go }) {
  const replyTo = params[0] ? store.inbox().find((m) => m.id === params[0]) : null;
  let to = replyTo ? store.person(replyTo.from) : null;
  const subject = replyTo ? 'Re: ' + replyTo.subject : '';

  return {
    html: fragment("compose", "screen", [icon('close'), esc(subject), icon('clip', 18), icon('send', 20)]),
    mount(root) {
      const box = root.querySelector('#to-box');
      const f = root.querySelector('#f');
      const err = root.querySelector('#err');
      let pickerHits = [];
      let pickerActive = -1;

      const drawTo = () => {
        root.querySelector('#to-hint').hidden = !!to;
        if (to) {
          box.innerHTML = fragment("compose", "to-x", [esc(to.name), icon('close', 18), esc(to.role)]);
          box.querySelector('#to-x').addEventListener('click', () => { to = null; drawTo(); root.querySelector('#to').focus(); });
          return;
        }
        box.innerHTML = fragment("compose", "to", [icon('users', 18)]);
        box.querySelector('#pick').addEventListener('click', () => {
          const GROUPS = [['a', 'Администрация'], ['t', 'Учителя'], ['m', 'Специалисты'], ['c', 'Классы целиком'], ['s', 'Ученики'], ['p', 'Родители']];
          const all = store.people();
          sheet(fragment("compose", "row", [icon('close', 18), GROUPS.map(([k, title]) => { const list = all.filter((p) => p.id.startsWith(k + '-')); return list.length ? fragment("compose", "card", [title, list.length, list.map((p) => ("<button type=\"button\" data-pid=\"" + (p.id) + "\"><b>" + (esc(p.name)) + "</b><span class=\"muted small\">" + (esc(p.role)) + "</span></button>")).join('')]) : ''; }).join('')]), (el, close) => el.querySelectorAll('[data-pid]').forEach((b) => b.addEventListener('click', () => {
            to = store.person(b.dataset.pid); close(); drawTo(); root.querySelector('#subj').focus();
          })));
        });
        // Combobox как в админке (classes.js): открыт по фокусу, фильтрует с
        // первого символа, закрывается по blur и Escape.
        const picker = box.querySelector('#tob');
        const inp = box.querySelector('#to');
        const list = box.querySelector('#sug');
        const show = (open) => {
          list.hidden = !open;
          picker.classList.toggle('open', open);
          inp.setAttribute('aria-expanded', String(open));
          if (!open) { pickerActive = -1; inp.removeAttribute('aria-activedescendant'); }
        };
        const pick = (index) => {
          const person = pickerHits[index];
          if (!person) return;
          to = store.person(person.id); drawTo(); root.querySelector('#subj').focus();
        };
        const draw = (query) => {
          const needle = query.trim().toLowerCase();
          pickerHits = store.people()
            .filter((p) => !needle || p.name.toLowerCase().includes(needle) || p.role.toLowerCase().includes(needle))
            .slice(0, 6);
          pickerActive = -1;
          inp.removeAttribute('aria-activedescendant');
          show(!!pickerHits.length);
          list.innerHTML = pickerHits.map((p, i) => ("<button type=\"button\" role=\"option\" id=\"opt-" + i + "\" aria-selected=\"false\"><b>" + (esc(p.name)) + "</b><span class=\"muted small\">" + (esc(p.role)) + "</span></button>")).join('');
          list.querySelectorAll('button').forEach((b, i) => b.addEventListener('click', () => pick(i)));
        };
        list.addEventListener('mousedown', (e) => e.preventDefault());
        inp.addEventListener('focus', () => draw(''));
        inp.addEventListener('input', () => draw(inp.value));
        inp.addEventListener('blur', () => show(false));
        inp.addEventListener('keydown', (e) => {
          if (e.key === 'Escape') { show(false); return; }
          if (list.hidden || !pickerHits.length) return;
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            pickerActive = (pickerActive + (e.key === 'ArrowDown' ? 1 : -1) + pickerHits.length) % pickerHits.length;
            inp.setAttribute('aria-activedescendant', 'opt-' + pickerActive);
            list.querySelectorAll('button').forEach((b, i) => { b.setAttribute('aria-selected', String(i === pickerActive)); b.classList.toggle('active', i === pickerActive); });
            return;
          }
          if (e.key === 'Enter' && pickerActive >= 0) { e.preventDefault(); pick(pickerActive); }
        });
      };
      drawTo();

      root.querySelector('#close').addEventListener('click', (e) => {
        if (f.text.value.trim() && !window.confirm('Закрыть письмо? Текст не сохранится.')) e.preventDefault();
      });

      const uploader = createFileList({
        input: root.querySelector('#file'),
        drop: f,
        list: root.querySelector('#files'),
      });

      f.addEventListener('submit', (e) => {
        e.preventDefault();
        const subj = f.subj.value.trim(), text = f.text.value.trim();
        if (!to) { err.textContent = 'Выберите, кому отправить письмо.'; err.hidden = false; return; }
        if (!subj && !text) { err.textContent = 'Напишите тему или текст письма.'; err.hidden = false; return; }
        if (uploader.pending()) { err.textContent = 'Подождите, файлы ещё загружаются.'; err.hidden = false; return; }
        if (!store.send({ to: to.id, subject: subj || '(без темы)', body: text, files: uploader.files.slice() })) { err.textContent = store.SAVE_ERROR; err.hidden = false; return; }
        toast('Письмо отправлено');
        go('mail/sent');
      });
    }
  };
}
export { composeView };
