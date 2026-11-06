import QRCode from './vendor/qr/index.js';
import { esc, sheet, toast } from './ui.js';
import * as store from './store.js';

// Реальный URL бота задают разработчики при подключении MAX. Коды в URL не передаём.
export const MAX_BOT_URL = '';
export function invitationBotUrl() {
  if (MAX_BOT_URL) return MAX_BOT_URL;
  const url = new URL(location.href);
  url.search = '?demo-bot=1'; url.hash = '#/login';
  return url.href;
}
export function qrSvg(url) {
  const qr = new QRCode(-1, 0); // M, тихая зона 4 модуля.
  qr.addData(url); qr.make();
  const size = qr.getModuleCount(), paths = [];
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) if (qr.isDark(y, x)) paths.push(`M${x + 4},${y + 4}h1v1h-1z`);
  return `<svg class="invitation-qr" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${size + 8} ${size + 8}" role="img" aria-label="QR: открыть бота Тайминг" shape-rendering="crispEdges"><rect width="100%" height="100%" fill="#fff"/><path d="${paths.join('')}" fill="#000"/></svg>`;
}
export function invitationCards(cards, url, demo = !MAX_BOT_URL) {
  const qr = qrSvg(url);
  return '<div class="invitation-grid">' + cards.map((card) => '<article class="invitation-card"><div class="invitation-meta">Тайминг · ' + esc(store.SCHOOL.name) + '</div><h3>' + esc(card.name) + '</h3><p>Класс ' + esc(card.className) + ' · ' + (card.role === 'parent' ? 'Родитель' : 'Ученик') + '</p><code>' + esc(card.code) + '</code><a class="invitation-qr-link" href="' + esc(url) + '" aria-label="Открыть бота Тайминг">' + qr + '</a><ol><li>Откройте бота «Тайминг» в MAX по QR и отправьте код.</li><li>Придумайте логин и задайте пароль в боте. Для ещё одного ребёнка отправьте код в тот же аккаунт.</li><li>Войдите на сайте: логин и пароль или «Войти через MAX» → «Вход» в боте → логин и разовый код.</li></ol>' + (demo ? '<p class="invitation-demo">Демо: QR открывает симуляцию бота в этом браузере.</p>' : '') + '</article>').join('') + '</div>';
}
export function showClassInvitations(classId) {
  const cards = store.classInvitations(classId);
  if (!cards) return toast(store.SAVE_ERROR);
  if (!cards.length) return toast('Нет приглашений для печати: все уже вошли или класс пуст.');
  const html = invitationCards(cards, invitationBotUrl());
  sheet('<div class="invitation-print-controls stack-sm"><h2 class="h2">Приглашения · ' + esc(store.classes()[classId].name) + '</h2><p class="hint">Только те, кто ещё не вошёл. Карточки учеников и родителей, сетка на A4.</p><button class="btn" id="go-print" type="button">Печать</button><button class="btn ghost" type="button" data-close>Закрыть</button></div>' + html, (el) => {
    el.classList.add('invitation-preview');
    el.querySelector('#go-print').addEventListener('click', () => {
      // Отдельная область печати исключает экран и ограниченный по высоте диалог.
      const area = document.createElement('div'); area.id = 'invitation-print-area'; area.innerHTML = html;
      document.getElementById(area.id)?.remove(); document.body.appendChild(area);
      document.body.classList.add('printing-invitations');
      const cleanup = () => { area.remove(); document.body.classList.remove('printing-invitations'); window.removeEventListener('afterprint', cleanup); };
      window.addEventListener('afterprint', cleanup);
      try { window.print(); } catch { cleanup(); toast('Не удалось открыть печать. Попробуйте ещё раз.'); }
    });
  });
}
