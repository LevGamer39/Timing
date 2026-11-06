import { fragment } from './templates.js';
import * as store from './store.js';
import { esc, plural, dayMonth, dayMonthShort } from './ui.js';
import { header, childSwitch, bindChildSwitch } from './common.js';

const LABEL = {
  pending: 'в работе',
  in_progress: 'в работе',
  done: 'сделано, ждёт проверки',
  accepted: 'принято',
  overdue: 'ожидает закрытия'
};

const label = (status) => LABEL[status] || 'в работе';
const ageText = (days) => (days == null ? '' : ' · ' + days + ' ' + plural(days, 'день', 'дня', 'дней'));

const item = (task) => '<li><span class="stat-name" title="' + esc(task.subject || task.title) + '">' + esc(task.title) + '</span>'
  + '<span class="stat-state">' + label(task.status) + ageText(task.age_days) + '</span></li>';

const list = (items, empty) => (items && items.length
  ? '<ul class="stat-list">' + items.map(item).join('') + '</ul>'
  : '<p class="muted small" style="margin: 0">' + empty + '</p>');

const heading = (text) => '<h2 style="margin: 0; font-size: 18px">' + text + '</h2>';

const dayBlock = (data) => '<div class="card pad stat-card">'
  + '<div class="stat-head">' + heading('Сегодня') + '<span class="muted small">' + dayMonth(new Date()) + '</span></div>'
  + list(data.tasks || [], 'Заданий на сегодня нет.')
  + '<div class="stat-head" style="margin-top: 12px">' + heading('Ждёт закрытия') + '</div>'
  + list(data.waiting_tasks || [], 'Ничего не висит — всё закрыто.')
  + '<div class="stat-head" style="margin-top: 12px">' + heading('Завтра') + '</div>'
  + list(data.tomorrow || [], 'На завтра ничего не задано.')
  + '</div>';

const weekBlock = (week) => {
  if (!week.total) {
    return heading('Неделя')
      + '<p class="muted small" style="margin: 0">Заданий с дедлайном на этой неделе пока нет.</p>';
  }
  const hanging = (week.waiting || 0) + (week.open || 0);
  const pill = week.week_ok
    ? '<span class="week-pill ok">Неделя в норме</span>'
    : '<span class="week-pill wait">' + hanging + ' ' + plural(hanging, 'задание ждёт', 'задания ждут', 'заданий ждут') + ' закрытия</span>';
  return heading('Неделя') + pill
    + '<p class="hint" style="margin: 0">В срок: ' + (week.on_time || 0)
    + ' · позже: ' + (week.late || 0)
    + ' · ещё открыто: ' + (week.open || 0) + '</p>'
    + '<p class="hint" style="margin: 0">Принято учителем: ' + (week.accepted || 0)
    + ' из ' + (week.total || 0) + '</p>';
};

const dynBlock = (data) => {
  const weeks = data.weeks || [];
  const rows = weeks.map((w) => {
    const total = w.on_time + w.late + w.open;
    const pct = (v) => (total ? Math.round(v / total * 100) : 0);
    return '<div class="bar-row"><span>' + dayMonthShort(new Date(w.week_start + 'T00:00:00')) + '</span>'
      + '<span class="bar bar-stack">'
      + (w.on_time ? '<i class="g5" style="width:' + pct(w.on_time) + '%"></i>' : '')
      + (w.late ? '<i class="g2" style="width:' + pct(w.late) + '%"></i>' : '')
      + (w.open ? '<i class="g3" style="width:' + pct(w.open) + '%"></i>' : '')
      + '</span>'
      + '<span class="hint">' + total + ' ' + plural(total, 'задание', 'задания', 'заданий') + '</span></div>';
  }).join('');

  const subjects = data.subjects || [];
  const max = Math.max(1, ...subjects.map((s) => s.count));
  const subjRows = subjects.map((s) => '<div class="bar-row"><span>' + esc(s.subject) + '</span>'
    + '<span class="bar"><i class="g5" style="width:' + Math.round(s.count / max * 100) + '%"></i></span>'
    + '<span class="hint">' + s.count + '</span></div>').join('');

  return heading('Динамика')
    + '<div class="bars">' + (rows || '<p class="muted small" style="margin: 0">Пока нечего показать.</p>') + '</div>'
    + (subjRows ? '<p class="hint" style="margin: 12px 0 0">По предметам</p><div class="bars">' + subjRows + '</div>' : '');
};

function statsView({ rerender }) {
  const server = store.isServer();
  const empty = '<div class="card pad"><p class="muted" style="margin: 0">Статистика доступна после входа в аккаунт: цифры считаются на сервере.</p></div>';

  const mount = (root) => {
    bindChildSwitch(root, rerender);
    const day = root.querySelector('#st-day');
    const week = root.querySelector('#st-week');
    const dyn = root.querySelector('#st-dyn');
    const ai = root.querySelector('#st-ai');
    if (!server) {
      day.innerHTML = empty;
      week.remove(); dyn.remove(); ai.remove();
      return;
    }

    Promise.all([store.statsDaily(), store.statsWeekly(), store.statsDynamics(8)])
      .then(([daily, weekly, dynamics]) => {
        day.innerHTML = dayBlock(daily);
        week.innerHTML = weekBlock(weekly);
        dyn.innerHTML = dynBlock(dynamics);
      })
      .catch(() => { day.innerHTML = '<div class="card pad"><p class="muted" style="margin: 0">Не удалось загрузить статистику.</p></div>'; });

    store.statsInsight()
      .then((insight) => { ai.innerHTML = heading('Что видно по неделям') + '<p class="stat-ai">' + esc(insight.text) + '</p>'; })
      .catch(() => ai.remove());
  };

  return {
    html: fragment('stats', 'page', [header(), childSwitch(), '', '', '', '']),
    mount
  };
}

export { statsView };

