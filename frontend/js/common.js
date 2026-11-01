import * as store from './store.js';
import { esc, icon, logo, sheet } from './ui.js';

export function notificationButton() {
  if (!store.isServer()) return '';
  return `<button class="icon-btn" id="notif" type="button" aria-label="Уведомления">${icon('bell', 20)}</button>`;
}

export function openNotifications() {
  if (!store.isServer()) return;
  sheet(`
<div class="row"><h2 class="h2 grow">Уведомления</h2><button class="icon-btn" type="button" data-close aria-label="Закрыть">${icon('close', 18)}</button></div>
<div id="nl" class="stack-sm" style="margin-top: 12px"><p class="muted">Загружаю…</p></div>
<button class="btn ghost sm" id="nreadall" type="button" style="margin-top: 12px">Отметить всё прочитанным</button>`, (el) => {
    const list = el.querySelector('#nl');
    const draw = (items) => {
      list.innerHTML = items.length ? items.map((n) => {
        const date = n.created_at ? new Date(n.created_at).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' }) : '';
        return '<button class="list-row" type="button" data-n="' + n.id + '" style="padding-left: 0; padding-right: 0; text-align: left">'
          + '<span class="grow"><b>' + esc(n.title) + '</b><br><span class="muted small">' + esc(n.body) + '</span><br><span class="hint">' + esc(date) + '</span></span>'
          + (n.is_read ? '' : '<span class="pill accent">Новое</span>') + '</button>';
      }).join('') : '<div class="empty-day">Уведомлений пока нет</div>';
      list.querySelectorAll('[data-n]').forEach((b) => b.addEventListener('click', () => {
        const item = items.find((n) => String(n.id) === b.dataset.n);
        if (!item || item.is_read) return;
        store.readNotification(item.id).then(() => { item.is_read = true; draw(items); });
      }));
    };
    store.notifications().then((items) => {
      draw(items);
      el.querySelector('#nreadall').addEventListener('click', () => {
        store.readAllNotifications().then(() => { items.forEach((n) => { n.is_read = true; }); draw(items); });
      });
    });
  });
}

export function header() {
  const learner = ['student', 'parent'].includes(store.role());
  return `
<header class="top brandbar">
  <a class="brand" href="#/home" aria-label="Вектор, на главную">${logo(28)}<span>Вектор</span></a>
  ${learner ? `<a class="btn ai-secondary sm" href="#/ai">${icon('spark', 18)}Спросить ИИ</a>` : ''}
  ${notificationButton()}
  <a class="icon-btn gear" href="#/settings" aria-label="Настройки">${icon('gear', 20)}</a>
</header>`;
}

export function childSwitch() {
  if (store.role() !== 'parent') return '';
  const s = store.student();
  const many = store.children().length > 1;
  return `
<button class="card row" id="child" type="button" style="width: 100%; min-height: 56px; padding: 8px 16px; text-align: left; border-radius: 20px" ${many ? '' : 'disabled'}>
  <span class="avatar" style="width: 36px; height: 36px; font-size: 13px; background: var(--soft); color: var(--text)">${esc(s.initials)}</span>
  <span class="grow"><b>${esc(s.first)}</b> <span class="muted">· ${esc(s.cls)} класс</span></span>
  ${many ? '<span class="muted small" style="font-weight: 700">Сменить</span>' + icon('down', 18) : ''}
</button>`;
}

export function bindChildSwitch(root, rerender) {
  const btn = root.querySelector('#child');
  if (!btn) return;
  btn.addEventListener('click', () => {
    const cur = store.student().id;
    sheet(`
<h2 class="h2" style="margin-bottom: 8px">Чей дневник показать</h2>
<div class="card lessons">
  ${store.children().map((c) => `
  <button class="list-row" type="button" data-child="${c.id}" aria-pressed="${c.id === cur}">
    <span class="avatar" style="width: 40px; height: 40px; font-size: 14px">${esc(c.initials)}</span>
    <span class="grow"><b>${esc(c.first)}</b><br><span class="muted small">${esc(c.cls)} класс</span></span>
    ${c.id === cur ? '<span class="pill">Сейчас</span>' : ''}
  </button>`).join('')}
</div>`, (el, close) => {
      el.querySelectorAll('[data-child]').forEach((b) => b.addEventListener('click', () => {
        store.setChild(b.dataset.child);
        close();
        rerender();
      }));
    });
  });
}

export function confirmSheet(title, text, okLabel, onOk, danger) {
  sheet(`
<h2 class="h2" style="font-size: 20px">${esc(title)}</h2>
<p class="muted" style="margin: 8px 0 16px">${esc(text)}</p>
<div class="row" style="justify-content: flex-end; gap: 8px">
  <button class="btn ghost sm" type="button" data-close>Отмена</button>
  <button class="btn sm" type="button" id="ok"${danger ? ' style="background: var(--danger); color: #fff"' : ''}>${esc(okLabel)}</button>
</div>`, (el, close) => el.querySelector('#ok').addEventListener('click', () => { close(); onOk(); }));
}
