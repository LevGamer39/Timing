import { fragment } from './templates.js';
import * as store from './store.js';
import { esc, icon, toast, sheet } from "./ui.js";

function composeView({ params, go }) {
  const replyTo = params[0] ? store.inbox().find((m) => m.id === params[0]) : null;
  let to = replyTo ? store.person(replyTo.from) : null;
  const subject = replyTo ? 'Re: ' + replyTo.subject : '';
  const files = [];

  return {
    html: fragment("compose", "screen", [icon('close'), esc(subject), icon('clip', 18), icon('send', 20)]),
    mount(root) {
      const box = root.querySelector('#to-box');
      const f = root.querySelector('#f');
      const err = root.querySelector('#err');

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
        const inp = box.querySelector('#to');
        const sug = box.querySelector('#sug');
        inp.addEventListener('input', () => {
          const q = inp.value.trim().toLowerCase();
          const hits = q.length < 2 ? [] : store.people().filter((p) => p.name.toLowerCase().includes(q) || p.role.toLowerCase().includes(q)).slice(0, 6);
          sug.hidden = !hits.length;
          inp.setAttribute('aria-expanded', String(!!hits.length));
          sug.innerHTML = hits.map((p) => ("<button type=\"button\" role=\"option\" data-id=\"" + (p.id) + "\"><b>" + (esc(p.name)) + "</b><span class=\"muted small\">" + (esc(p.role)) + "</span></button>")).join('');
          sug.querySelectorAll('button').forEach((b) => b.addEventListener('click', () => { to = store.person(b.dataset.id); drawTo(); root.querySelector('#subj').focus(); }));
        });
      };
      drawTo();

      root.querySelector('#close').addEventListener('click', (e) => {
        if (f.text.value.trim() && !window.confirm('Закрыть письмо? Текст не сохранится.')) e.preventDefault();
      });

      root.querySelector('#file').addEventListener('change', (e) => {
        [...e.target.files].forEach((x) => files.push(x.name));
        root.querySelector('#files').innerHTML = files.map((n) => ("<span class=\"pill\">" + (icon('clip', 14)) + (esc(n)) + "</span>")).join('');
      });

      f.addEventListener('submit', (e) => {
        e.preventDefault();
        const subj = f.subj.value.trim(), text = f.text.value.trim();
        if (!to) { err.textContent = 'Выберите, кому отправить письмо.'; err.hidden = false; return; }
        if (!subj && !text) { err.textContent = 'Напишите тему или текст письма.'; err.hidden = false; return; }
        if (!store.send({ to: to.id, subject: subj || '(без темы)', body: text, files: files.slice() })) { err.textContent = store.SAVE_ERROR; err.hidden = false; return; }
        toast('Письмо отправлено');
        go('mail/sent');
      });
    }
  };
}
export { composeView };
