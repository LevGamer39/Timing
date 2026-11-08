import { fragment } from './templates.js';
import * as store from './store.js';
import { esc, gradePill, dayMonth, dayMonthShort, DAYS, weekday, plural, ddmm, iso, icon, toast } from './ui.js';
import { header, childSwitch, bindChildSwitch } from './common.js';

function homeView({ rerender }) {
  const acc = store.account();
  const s = store.student();
  const isParent = store.role() === 'parent';
  const now = store.NOW;
  const next = store.nextLesson();
  const tomorrow = store.tomorrowHomework();
  const fresh = store.newGrades();
  const today = store.week(0).days.find((d) => d.date === iso(now));

  const todayRows = today?.lessons?.map((l) => {
    const grades = l.grades.map((g) => gradePill(g)).join('') + (l.absent ? '<span class="g gn" title="Пропуск" aria-label="Пропуск">Н</span>' : '');
    if (next && next.lesson.n === l.n) {
      const when = next.started ? 'идёт сейчас' : 'через ' + next.inMin + ' ' + plural(next.inMin, 'минуту', 'минуты', 'минут');
      return fragment('home', 'card', [l.n, next.started ? 'сейчас' : 'следующий', when, esc(l.name), esc(l.time), /^\d/.test(l.room) ? 'каб. ' : '', esc(l.room), grades ? '<div class="ls-g">' + grades + '</div>' : '']);
    }
    return fragment('home', 'ls', ['', l.n, esc(l.name), esc(l.time + (l.room ? ' · ' + (/^\d/.test(l.room) ? 'каб. ' : '') + l.room : '')), grades, '']);
  }).join('') || '<div class="empty-day">' + esc(today?.off || 'Сегодня уроков нет') + '</div>';
  const todayBlock = fragment('home', 'only-desk', [todayRows, icon('calendar', 20)]);

  const shortDays = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'];
  const hwLabel = tomorrow ? shortDays[weekday(tomorrow.day.d)] + ', ' + dayMonthShort(tomorrow.day.d) : '';
  const nextDay = new Date(now); nextDay.setDate(nextDay.getDate() + 1);
  const hwTitle = !tomorrow || tomorrow.day.date === iso(nextDay) ? 'Домашка на завтра' : 'Домашка на ' + ['понедельник', 'вторник', 'среду', 'четверг', 'пятницу', 'субботу', 'воскресенье'][weekday(tomorrow.day.d)];
  const items = tomorrow?.items || [];
  const done = items.filter((l) => l.done).length;
  const hwRows = tomorrow?.day.off
    ? '<div class="empty-day">Завтра: ' + esc(tomorrow.day.off.toLowerCase()) + '</div>'
    : items.length ? items.map((l) => isParent
      ? '<div class="list-row home-hw-row home-parent-hw"><b>' + esc(l.name) + '</b><span class="small">' + esc(l.hw) + '</span></div>'
      : fragment('home', 'list-row', [esc(l.hwKey), l.done ? 'checked' : '', esc(l.name), esc(l.hw)])).join('')
      : '<div class="empty-day">Ничего не задано</div>';
  const hwFooter = !isParent && items.length
    ? '<p class="home-card-footer small muted" id="hwc">Сделано ' + done + ' из ' + items.length + ' · отметки видишь только ты</p>' : '';
  const hwBlock = fragment('home', 'section-h-2', [esc(hwLabel), hwRows, hwFooter, icon('diary', 20), hwTitle]);

  const hol = store.nextHolidays();
  const holBlock = hol ? '<p class="home-holidays muted small">' + (hol.now
    ? 'Каникулы до ' + dayMonth(store.parse(hol.to))
    : 'До каникул ' + hol.days + ' ' + plural(hol.days, 'день', 'дня', 'дней') + ' · ' + dayMonth(store.parse(hol.from)) + ' – ' + dayMonth(store.parse(hol.to))) + '</p>' : '';

  const gradesBlock = fragment('home', 'section-h-3', [fresh.length ? '<span class="pill">' + fresh.length + '</span>' : '',
    fresh.length ? fragment('home', 'seen', [fresh.slice(0, 4).map((g) => fragment('home', 'list-row-2', [g.subj, esc(store.SUBJECTS[g.subj].name), esc(g.type), ddmm(g.date), gradePill(g)])).join('')])
      : '<div class="empty-day">Новых оценок нет</div><div class="home-card-footer"><a class="home-text-action" href="/grades">Открыть оценки</a></div>', icon('grades', 20)]);

  return {
    html: fragment("home", "stack", [header(), DAYS[weekday(now)], dayMonth(now), isParent ? 'Здравствуйте, ' + esc(acc.first) : 'Привет, ' + esc(s.first), childSwitch(), todayBlock, hwBlock, gradesBlock, holBlock]),
    mount(root) {
      bindChildSwitch(root, rerender);
      root.querySelectorAll('[data-hw]').forEach((cb) => cb.addEventListener('change', () => {
        if (!store.toggleHw(cb.dataset.hw, cb.checked)) { cb.checked = !cb.checked; toast(store.SAVE_ERROR); return; }
        const all = root.querySelectorAll('[data-hw]');
        const c = root.querySelector('#hwc');
        if (c) c.textContent = 'Сделано ' + [...all].filter((x) => x.checked).length + ' из ' + all.length + ' · отметки видишь только ты';
      }));
      const seen = root.querySelector('#seen');
      if (seen) seen.addEventListener('click', () => { store.markSeen(); rerender(); });
    }
  };
}
export { homeView };
