import { fragment } from './templates.js';
import * as store from './store.js';
import { esc, icon, mailDate, dayMonth, toast } from "./ui.js";
import { header, confirmSheet } from './common.js';
import { query, setQuery } from "./shared/mail.js";

const initials = (name) => name.split(' ').slice(0, 2).map((w) => w[0]).join('').toUpperCase();

const attachmentsOf = (m) => (Array.isArray(m.files) ? m.files : m.file ? [m.file] : []).map((f) => (typeof f === 'string' ? { name: f, token: '' } : f));

let failedReadId = null;
store.onContextChange(() => { failedReadId = null; });

function mailView({ params, path, rerender }) {
  const readId = path.startsWith('mail/read/') ? params[0] : null;
  const inMsg = readId ? store.inbox().find((m) => m.id === readId) : null;
  const outMsg = readId && !inMsg ? store.sent().find((m) => m.id === readId) : null;
  const letter = inMsg || outMsg;
  if (failedReadId !== readId) failedReadId = null;
  const readError = inMsg && inMsg.unread && failedReadId !== readId && !store.markRead(readId);
  if (readError) failedReadId = readId;
  const tab = outMsg || params[0] === 'sent' ? 'sent' : 'in';
  const q = query.trim().toLowerCase();
  const match = (m, who) => !q || (who.name + ' ' + m.subject + ' ' + m.body).toLowerCase().includes(q);
  const now = store.NOW;
  const desk = window.matchMedia('(min-width: 1024px)').matches;

  const kind = (id) => ({ t: 'teacher', a: 'admin', s: 'student', p: 'parent', c: 'student' })[(id || '').split('-')[0]] || 'student';
  const row = (m, who, whoId, unread) => fragment("mail", "mrow", [unread ? ' unread' : '', m.id, m.id === readId ? ' aria-current="true"' : '', kind(whoId), esc(initials(who.name)), tab === 'sent' ? 'Кому: ' : '', esc(who.name), esc(mailDate(m.date, now)), unread ? ("<span class=\"dot-new\" aria-label=\"не прочитано\"></span>") : '', attachmentsOf(m).length ? icon('clip', 15) : '', esc(m.subject), esc(m.body)]);
  const groupOf = (d) => { const t = mailDate(d, now); return /^\d\d:\d\d$/.test(t) ? 'Сегодня' : t === 'Вчера' ? 'Вчера' : 'Раньше'; };
  const groups = (list, who, whoId, unreadOf) => {
    const out = [];
    ['Сегодня', 'Вчера', 'Раньше'].forEach((g) => {
      const items = list.filter((m) => groupOf(m.date) === g);
      if (items.length) out.push(("<div class=\"mgroup\">" + (g) + "</div>") + items.map((m) => row(m, who(m), whoId(m), unreadOf(m))).join(''));
    });
    return ("<div class=\"card mail-box\">" + (out.join('')) + "</div>");
  };

  const empty = (text) => '<div class="card pad empty-state"><p>' + text + '</p>' + (q
    ? '<button class="text-action" type="button" data-clear-search>Сбросить поиск</button>'
    : '<a class="text-action" href="#/mail/new">Написать</a>') + '</div>';
  let body;
  if (tab === 'in') {
    const all = store.inbox().filter((m) => match(m, store.person(m.from)));
    body = all.length ? groups(all, (m) => store.person(m.from), (m) => m.from, (m) => m.unread)
      : empty(q ? 'Ничего не найдено' : 'Писем пока нет');
  } else {
    const all = store.sent().filter((m) => match(m, store.person(m.to)));
    body = all.length ? groups(all, (m) => store.person(m.to), (m) => m.to, () => false)
      : empty(q ? 'Ничего не найдено' : 'Отправленных писем пока нет');
  }
  let read;
  if (letter) {
    const who = store.person(inMsg ? letter.from : letter.to);
    const d = new Date(letter.date);
    read = fragment("mail", "del", [inMsg ? '' : '/sent', icon('back'), inMsg ? 'Входящие' : 'Отправленные', esc(letter.subject), kind(inMsg ? letter.from : letter.to), esc(initials(who.name)), inMsg ? '' : 'Кому: ', esc(who.name), esc(who.role), dayMonth(d), d.toTimeString().slice(0, 5), esc(letter.body), attachmentsOf(letter).map((file) => fragment("mail", "row", [icon('clip'), esc(file.name), esc(file.token || '')])).join(''), inMsg ? ("<a class=\"btn\" href=\"#/mail/new/" + (letter.id) + "\">" + (icon('reply', 20)) + "Ответить</a>") : '', icon('trash', 20)]);
  } else {
    read = ("<div class=\"card read-empty\"><div>" + (icon('mail', 40)) + "<p style=\"margin: 12px 0 0; font-weight: 600\">Выберите письмо слева</p></div></div>");
  }

  return {
    html: fragment("mail", "q", [letter ? ' has-letter' : '', desk ? ' data-keep-scroll' : '', header(), icon('pen', 18), esc(query), tab === 'in', store.unreadCount() ? ' · ' + store.unreadCount() : '', tab === 'sent', body, read, letter ? '' : ("<a class=\"btn fab\" href=\"#/mail/new\">" + (icon('pen', 20)) + "Написать</a>")]),
    mount(root) {
      if (readError) toast(store.SAVE_ERROR);
      root.querySelectorAll('[data-tab]').forEach((b) => b.addEventListener('click', () => { location.hash = '#/' + b.dataset.tab; }));
      const qi = root.querySelector('#q');
      root.querySelector('[data-clear-search]')?.addEventListener('click', () => { setQuery(''); rerender(); document.getElementById('q')?.focus(); });
      qi.addEventListener('input', () => {
        setQuery(qi.value);
        const pos = qi.selectionStart;
        rerender();
        const n = document.getElementById('q');
        n.focus(); n.setSelectionRange(pos, pos);
      });
      root.querySelectorAll('[data-open-file]').forEach((b) => b.addEventListener('click', () => {
        const token = b.dataset.openFile;
        if (!token) return toast('Файл был приложен без загрузки — скачать его нельзя.');
        const name = b.closest('.row')?.querySelector('.grow')?.textContent || 'файл';
        store.downloadAttachment(token, name).catch(() => toast('Не удалось скачать файл'));
      }));
      const del = root.querySelector('#del');
      if (del) del.addEventListener('click', () => confirmSheet('Удалить письмо?', 'Письмо «' + letter.subject + '» пропадёт из почты.', 'Удалить', () => {
        if (!store.deleteMail(letter.id)) return toast(store.SAVE_ERROR);
        toast('Письмо удалено'); location.hash = inMsg ? '#/mail' : '#/mail/sent';
      }, true));
    }
  };
}
export { mailView };
