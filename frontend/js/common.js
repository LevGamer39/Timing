import * as store from './store.js';
import { esc, icon, logo, sheet } from './ui.js';

export function header() {
  const learner = ['student', 'parent'].includes(store.role());
  return `
<header class="top brandbar">
  <a class="brand" href="#/home" aria-label="Вектор, на главную">${logo(28)}<span>Вектор</span></a>
  ${learner ? `<a class="btn ai-secondary sm" href="#/ai">${icon('spark', 18)}Спросить ИИ</a>` : ''}
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
