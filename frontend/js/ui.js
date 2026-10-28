import { score, gradeMark, normalizedBounds, gradeScale, storedScore } from './grading.js';

export const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
export const DAYS = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];

export const iso = (d) => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
export const parse = (s) => { const [y, m, d] = s.slice(0, 10).split('-').map(Number); return new Date(y, m - 1, d); };
export const addDays = (d, n) => { const r = new Date(d); r.setDate(r.getDate() + n); return r; };
export const weekday = (d) => (d.getDay() + 6) % 7;
export const mondayOf = (d) => { const r = new Date(d.getFullYear(), d.getMonth(), d.getDate()); return addDays(r, -weekday(r)); };
export const dayMonth = (d) => d.getDate() + ' ' + MONTHS[d.getMonth()];
export const dayMonthShort = (d) => d.getDate() + ' ' + MONTHS_SHORT[d.getMonth()];
export const ddmm = (s) => s.slice(8, 10) + '.' + s.slice(5, 7);

export function weekTitle(mon, days = 6) {
  const sat = addDays(mon, days - 1);
  return mon.getMonth() === sat.getMonth()
    ? mon.getDate() + ' – ' + dayMonth(sat)
    : dayMonth(mon) + ' – ' + dayMonth(sat);
}

export function mailDate(s, now) {
  const d = new Date(s);
  if (iso(d) === iso(now)) return d.toTimeString().slice(0, 5);
  if (iso(d) === iso(addDays(now, -1))) return 'Вчера';
  return dayMonthShort(d);
}

// Границы зон задаёт администратор («с какого балла 3, 4, 5»); по умолчанию 40 / 60 / 80.
export const MARK_FROM = { 5: 80, 4: 60, 3: 40 };
export function setBounds(b) { MARK_FROM[3] = b[0]; MARK_FROM[4] = b[1]; MARK_FROM[5] = b[2]; }
export const zone = (v) => v === 'Н' ? 'gn' : 'g' + gradeMark(v);
export const mark = gradeMark;
export const fmt = (n) => typeof n === 'number' && Number.isFinite(n) ? score(n, false).toFixed(1).replace('.', ',') : '—';

const SHAPES = {
  star: 'M12 3l2.6 5.6 6.1.7-4.5 4.2 1.2 6L12 16.6 6.6 19.5l1.2-6L3.3 9.3l6.1-.7z',
  moon: 'M15 3a9 9 0 1 0 6 15.5A7.5 7.5 0 0 1 15 3z',
  bolt: 'M13 2 4 14h7l-1 8 9-12h-7z',
  leaf: 'M5 19C5 10 11 4 20 4c0 9-6 15-15 15zm0 0 7-7',
  wave: 'M2 15c3 0 3-4 6-4s3 4 6 4 3-4 6-4M2 20c3 0 3-4 6-4s3 4 6 4 3-4 6-4',
  mount: 'M2 20 9 8l4 6 3-4 6 10z',
  book: 'M4 5h7a2 2 0 0 1 2 2v13H6a2 2 0 0 1-2-2zM20 5h-5v15h5z',
  planet: 'M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10zM2 15c3 2 17-3 20-8',
  heart: 'M12 20s-8-5-8-11a4 4 0 0 1 8-1 4 4 0 0 1 8 1c0 6-8 11-8 11z',
  sun: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2v3M12 19v3M2 12h3M19 12h3',
  rocket: 'M12 2c4 3 5 8 3 13H9C7 10 8 5 12 2zM9 15l-3 4M15 15l3 4M12 9.5h.01',
  note: 'M9 18V5l11-2v13M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0zM20 16a3 3 0 1 1-6 0 3 3 0 0 1 6 0z'
};
const AV_BG = ['#E0743C', '#1A1410', '#2FA36B', '#3B6FD8', '#7B5CE0', '#D94F70'];
export const AVATARS = Object.keys(SHAPES).map((shape, i) => ({ id: shape, bg: AV_BG[i % AV_BG.length] }));
const W = '#fff';
const EXTRA = [
  ['saturn', '#3B6FD8', '<circle cx="20" cy="20" r="7" fill="' + W + '"/><ellipse cx="20" cy="20" rx="14" ry="4.5" fill="none" stroke="' + W + '" stroke-width="2" transform="rotate(-20 20 20)"/>'],
  ['earth', '#2FA36B', '<circle cx="20" cy="20" r="11" fill="none" stroke="' + W + '" stroke-width="2"/><path d="M13 15c3 1 4 4 7 3s3 3 6 2M14 25c2-1 4 0 5 2" fill="none" stroke="' + W + '" stroke-width="2" stroke-linecap="round"/>'],
  ['mars', '#D94F70', '<circle cx="20" cy="20" r="10" fill="' + W + '"/><circle cx="16" cy="17" r="2" fill="#D94F70" opacity=".5"/><circle cx="23" cy="23" r="3" fill="#D94F70" opacity=".5"/>'],
  ['comet', '#1A1410', '<circle cx="25" cy="15" r="4" fill="' + W + '"/><path d="M22 18 9 31M24 20l-9 9M20 16l-9 9" stroke="' + W + '" stroke-width="2" stroke-linecap="round" opacity=".8"/>'],
  ['ufo', '#7B5CE0', '<ellipse cx="20" cy="22" rx="12" ry="4" fill="' + W + '"/><path d="M14 20a6 6 0 0 1 12 0" fill="none" stroke="' + W + '" stroke-width="2"/><path d="M15 29l-1 3M25 29l1 3M20 29v3" stroke="' + W + '" stroke-width="2" stroke-linecap="round"/>'],
  ['galaxy', '#1A1410', '<path d="M20 20c0-3 4-4 6-1s0 8-6 8-9-5-8-10 7-8 13-6" fill="none" stroke="' + W + '" stroke-width="2" stroke-linecap="round"/><circle cx="20" cy="20" r="1.5" fill="' + W + '"/>'],
  ['stars', '#3B6FD8', '<path d="M14 12l1.2 2.8 3 .3-2.3 2 .7 3-2.6-1.6-2.6 1.6.7-3-2.3-2 3-.3zM26 20l1 2.2 2.4.3-1.8 1.6.5 2.4-2.1-1.3-2.1 1.3.5-2.4-1.8-1.6 2.4-.3z" fill="' + W + '"/><circle cx="16" cy="27" r="1.2" fill="' + W + '"/>'],
  ['crescent', '#E0743C', '<path d="M23 10a10 10 0 1 0 7 17 8 8 0 0 1-7-17z" fill="' + W + '"/><circle cx="28" cy="12" r="1.3" fill="' + W + '"/>']
];
['#E0743C', '#1A1410', '#2FA36B', '#3B6FD8', '#7B5CE0', '#D94F70'].forEach((bg, i) => {
  EXTRA.push(['person' + (i + 1), bg, '<circle cx="20" cy="15" r="5.5" fill="' + W + '"/><path d="M10.5 32c0-6.5 4.3-10.5 9.5-10.5s9.5 4 9.5 10.5z" fill="' + W + '"/>']);
});
EXTRA.forEach(([id, bg]) => AVATARS.push({ id, bg }));

export function avatarSvg(id, size) {
  const a = AVATARS.find((x) => x.id === id);
  if (!a) return '';
  const head = '<svg aria-hidden="true" width="' + size + '" height="' + size + '" viewBox="0 0 40 40"><circle cx="20" cy="20" r="20" fill="' + a.bg + '"/>';
  const extra = EXTRA.find(([x]) => x === id);
  if (extra) return head + extra[2] + '</svg>';
  const filled = ['star', 'moon', 'bolt', 'mount', 'heart'].includes(a.id);
  return head + '<g transform="translate(8 8)"><path d="' + SHAPES[a.id] + '" fill="' + (filled ? '#fff' : 'none') + '" stroke="#fff" stroke-width="' + (filled ? 0 : 2) + '" stroke-linecap="round" stroke-linejoin="round"/></g></svg>';
}
export function weighted(list) {
  let s = 0, w = 0;
  list.forEach((g) => { s += g.value * g.w; w += g.w; });
  return w ? s / w : null;
}

export function gradePill(g, extra = '') {
  const label = esc(score(g.value) + ', вес ' + (g.w || 1));
  return '<span class="g ' + zone(g.value) + ' ' + extra + '" title="' + label + '" aria-label="' + label + '">' + score(g.value) + (g.w > 1 ? '<small aria-hidden="true">×' + g.w + '</small>' : '') + '</span>';
}

export function goalPlan(list, target) {
  const s = list.reduce((a, g) => a + g.value * g.w, 0);
  const w = list.reduce((a, g) => a + g.w, 0);
  const avg = w ? s / w : 0;
  if (avg >= target) return { reached: true, avg };
  const options = [];
  for (const [weight, label] of [[1, 'обычных работ'], [3, 'контрольных ×3']]) {
    for (let k = 1; k <= 8; k++) {
      const need = storedScore(Math.ceil((target * (w + k * weight) - s) / (k * weight) * gradeScale() / 100));
      if (need <= 100) { options.push({ k, weight, label, need: Math.max(need, 0) }); break; }
    }
  }
  return { reached: false, avg, options };
}

const plural = (n, a, b, c) => { const m = n % 10, h = n % 100; return m === 1 && h !== 11 ? a : m >= 2 && m <= 4 && (h < 10 || h >= 20) ? b : c; };
export { plural };

export function goalText(list, targetMark) {
  const target = normalizedBounds()[targetMark - 3];
  const plan = goalPlan(list, target);
  const now = 'Сейчас ' + fmt(plan.avg) + ' — это ' + mark(plan.avg) + '.';
  if (plan.reached) return now + '\nЦель уже достигнута: держи средний не ниже ' + score(target) + '. Следующие работы — от ' + score(target) + ' баллов.';
  if (!plan.options.length) return now + '\nДля ' + targetMark + ' нужен средний ' + score(target) + '. В этом периоде это уже очень сложно — можно поставить цель на год.';
  const lines = plan.options.map((o) => '• ' + o.k + ' ' + (o.weight === 1 ? plural(o.k, 'обычная работа', 'обычные работы', 'обычных работ') : plural(o.k, 'контрольная ×3', 'контрольные ×3', 'контрольных ×3')) + ' на ' + score(o.need) + '+');
  return now + '\nДля ' + targetMark + ' нужен средний ' + score(target) + '. Подойдёт, например:\n' + lines.join(',\nили\n') + '.';
}

const P = {
  home: '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
  diary: '<path d="M5 4h11a3 3 0 0 1 3 3v14H8a3 3 0 0 1-3-3z"/><path d="M5 18a3 3 0 0 1 3-3h11"/>',
  grades: '<path d="M3 21h18M6 17v-6M12 17V5M18 17V9"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
  spark: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
  back: '<path d="m15 6-6 6 6 6"/>',
  next: '<path d="m9 6 6 6-6 6"/>',
  close: '<path d="M6 6l12 12M18 6 6 18"/>',
  pen: '<path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>',
  send: '<path d="M4 12 20 4l-6 16-3-7z"/>',
  clip: '<path d="M21 11.5 12.5 20a5 5 0 0 1-7-7L14 4.5a3.5 3.5 0 0 1 5 5L10.5 18a2 2 0 0 1-3-3L15 7.5"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
  reset: '<path d="M4 12a8 8 0 1 0 2.4-5.7M4 4v4h4"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  logout: '<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 16l-4-4 4-4M6 12h10"/>',
  reply: '<path d="M10 9V5l-7 7 7 7v-4c5 0 8 1.5 10 5-1-5-4-10-10-11z"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M9 4v16M15 4v16"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  users: '<circle cx="9" cy="8" r="4"/><path d="M2 21a7 7 0 0 1 14 0M17 4a4 4 0 0 1 0 8M22 21a7 7 0 0 0-4-6.3"/>',
  teacher: '<circle cx="12" cy="7" r="4"/><path d="M5 21a7 7 0 0 1 14 0"/><path d="M16 3h5v4"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="m10.8 12.2 8.7-8.7M17 6l3 3M14 9l2 2"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  chat: '<path d="M4 5h16v11H9l-5 4z"/><path d="M8 10h8M8 13h5"/>',
  download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
  absent: '<circle cx="9" cy="8" r="4"/><path d="M2 21a7 7 0 0 1 14 0M17 9l4 4M21 9l-4 4"/>',
  print: '<path d="M6 9V3h12v6M6 18H4v-7h16v7h-2"/><rect x="6" y="14" width="12" height="7"/>'
};
export const icon = (name, size = 22) => '<svg aria-hidden="true" width="' + size + '" height="' + size + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + P[name] + '</svg>';

export const logo = (size = 28) => '<svg aria-hidden="true" width="' + size + '" height="' + Math.round(size * 0.76) + '" viewBox="-8 -9 620 470"><path class="logo-b" d="M137 0H535C577 0 604 31 604 70C604 87 598 100 591 113L383 428Q370 452 344 452H298Q267 452 278 426L440 154Q454 130 427 113H201Q183 113 174 99L122 36Q111 17 124 6Q130 0 137 0Z"/><path class="logo-a" d="M24 165H301C329 165 347 196 333 220L208 429Q194 452 169 452H127Q95 452 107 426L229 271Q242 250 221 250H57Q43 250 33 240L4 199Q-3 188 5 177Q12 165 24 165Z"/></svg>';

let toastTimer;
export function toast(text) {
  const el = document.getElementById('toast');
  el.hidden = false;
  el.textContent = text;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 2600);
}

const openSheets = new Set();
export const closeSheets = () => openSheets.forEach((close) => close());
export function sheet(html, onMount) {
  // Старое подтверждение не должно закрывать заголовок нового диалога.
  clearTimeout(toastTimer);
  const notification = document.getElementById?.('toast');
  if (notification) { notification.classList.remove('show'); notification.hidden = true; }
  const ov = document.createElement('div');
  ov.className = 'overlay';
  ov.innerHTML = '<div class="sheet" role="dialog" aria-modal="true"><div class="sheet-grip"></div>' + html + '</div>';
  const close = () => { ov.remove(); document.removeEventListener('keydown', onKey); openSheets.delete(close); };
  const onKey = (e) => { if (e.key === 'Escape' && [...document.querySelectorAll('.overlay')].at(-1) === ov) close(); };
  ov.addEventListener('click', (e) => { if (e.target === ov || e.target.closest('[data-close]')) close(); });
  document.addEventListener('keydown', onKey);
  openSheets.add(close);
  document.body.appendChild(ov);
  const first = ov.querySelector('button, a, input');
  if (first) first.focus();
  if (onMount) onMount(ov.querySelector('.sheet'), close);
  return close;
}
