import { fragment } from './templates.js';
import * as store from './store.js';
import { esc, icon, gradePill, weekTitle, dayMonthShort, DAYS, weekday, iso, plural, toast } from './ui.js';
import { header, childSwitch, bindChildSwitch } from './common.js';

function diaryView({ params, rerender }) {
  const offset = Number(params[0] || 0);
  const isParent = store.role() === 'parent';
  const { mon, days } = store.week(offset);
  const weekNo = Math.round((mon - new Date(2026, 7, 31)) / (7 * 864e5)) + 1;
  const today = iso(store.NOW);
  const lessonsCount = days.reduce((a, d) => a + (d.lessons ? d.lessons.length : 0), 0);
  const hwCount = days.reduce((a, d) => a + (d.lessons ? d.lessons.filter((l) => l.hw).length : 0), 0);
  const holiday = days.every((d) => d.off && d.off !== 'Выходной');
  const next = store.nextLesson();

  const lessonRow = (l, d) => {
    const isNow = next && d.date === today && next.lesson.n === l.n;
    const marks = l.grades.map((g) => gradePill(g)).join('') + (l.absent ? ("<span class=\"g gn\" title=\"Пропуск\" aria-label=\"Пропуск\">Н</span>") : '');
    const hw = !l.hw ? '' : isParent
      ? ("<div class=\"hw-text\">" + (esc(l.hw)) + "</div>")
      : ("<label class=\"hw\"><input type=\"checkbox\" data-hw=\"" + (esc(l.hwKey)) + "\" " + (l.done ? 'checked' : '') + "><span>" + (esc(l.hw)) + "</span></label>");
    return fragment("diary", "ls", [isNow ? ' now' : '', isNow ? ' id="now"' : '', l.n, l.subj, esc(l.name), esc(l.time), /^\d/.test(l.room) ? 'каб. ' : '', esc(l.room), isNow ? (" · <span class=\"now-tag\">") + (next.started ? 'идёт' : 'следующий') + ("</span>") : '', marks, hw]);
  };

  const daySection = (d) => fragment("diary", "day-h", [DAYS[weekday(d.d)], d.date === today ? ' id="today"' : '', DAYS[weekday(d.d)], d.date === today ? ("<span class=\"pill accent\">сегодня</span>") : '', dayMonthShort(d.d), d.off ? ("<div class=\"empty-day\">" + (esc(d.off)) + "</div>") : d.lessons.map((l) => lessonRow(l, d)).join('')]);

  return {
    html: fragment("diary", "stack", [header(), holiday ? 'каникулы' : 'неделя ' + weekNo, esc(weekTitle(mon, days.length)), offset - 1, icon('back'), offset + 1, icon('next'), offset !== 0 ? ("<a class=\"btn ghost sm\" href=\"#/diary/0\">К этой неделе</a>") : ("<span class=\"pill\">" + (lessonsCount) + " " + (plural(lessonsCount, 'урок', 'урока', 'уроков')) + " · " + (hwCount) + " ДЗ</span>"), childSwitch(), days.map(daySection).join('')]),
    mount(root) {
      bindChildSwitch(root, rerender);
      root.querySelectorAll('[data-hw]').forEach((cb) => cb.addEventListener('change', () => {
        if (!store.toggleHw(cb.dataset.hw, cb.checked)) { cb.checked = !cb.checked; toast(store.SAVE_ERROR); }
      }));
      const t = root.querySelector('#today');
      if (offset === 0 && t) {
        requestAnimationFrame(() => {
          // На компьютере сверху закреплена панель, под ней — переключатель ребёнка.
          const topbar = window.matchMedia('(min-width: 1024px)').matches ? document.querySelector('.topbar') : null;
          const context = root.querySelector('.child-context');
          const inset = (topbar ? topbar.getBoundingClientRect().height : 0) + (context ? context.getBoundingClientRect().height + 16 : 8);
          // Компьютер: сегодня уже в верхней части экрана (первый ряд недели) — не уводим заголовок и стрелки недели.
          const top = t.getBoundingClientRect().top;
          if (topbar && top >= inset && top <= window.innerHeight * 0.6) return;
          // Плавно — видно, откуда пришли к сегодняшнему дню; без анимации, если система просит меньше движения.
          const calm = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
          window.scrollTo({ top: Math.max(0, top + window.scrollY - inset), behavior: calm ? 'auto' : 'smooth' });
        });
      }
    }
  };
}
export { diaryView };
