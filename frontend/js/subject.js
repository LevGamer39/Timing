import { fragment } from './templates.js';
import * as store from './store.js';
import { esc, icon, gradePill, zone, mark, fmt, ddmm, goalText, plural, sheet } from "./ui.js";

import { period, periods, goalChat, setGoalChat } from "./shared/grades.js";
import { childSwitch, bindChildSwitch } from './common.js';

function subjectView({ params, go, rerender }) {
  const key = params[0];
  const p = periods().find((x) => x.key === period) || store.currentPeriod();
  const s = store.subjects(p.to, p.from).find((x) => x.key === key);
  if (!s) { go('grades'); return { html: '' }; }
  if (goalChat.subj !== key || goalChat.period !== p.key) setGoalChat({ subj: key, period: p.key, msgs: [] });
  const isParent = store.role() === 'parent';
  const avg = s.avg;
  const cur = avg != null ? mark(avg) : null;
  const items = s.list.slice().reverse();
  const quick = cur === 5 ? [['keep5', 'Удержать 5']] : [[String(Math.min(cur + 1, 5)), 'Хочу ' + Math.min(cur + 1, 5)], ...(cur < 4 ? [] : [['keep' + cur, 'Удержать ' + cur]])];
  if (cur !== null && cur < 4) quick.push(['5', 'Хочу 5']);

  const answer = (code) => {
    const target = code.startsWith('keep') ? Number(code.slice(4)) : Number(code);
    return goalText(s.list, target);
  };

  return {
    html: fragment("subject", "top", [icon('back'), esc(s.name), esc(s.teacher), avg != null ? fragment("subject", "card", [zone(avg), fmt(avg), cur, esc(p.name), s.list.length, plural(s.list.length, 'оценка', 'оценки', 'оценок')]) : ("<div class=\"card pad empty-state\"><p>Оценок по предмету пока нет</p><a class=\"text-action\" href=\"/grades\">Открыть оценки</a></div>"), items.length ? fragment("subject", "section-h", [items.map((g) => fragment("subject", "list-row", [esc(g.id), esc(g.type), ddmm(g.date), g.value, ddmm(g.date), esc(g.type), g.w > 1 ? (" <span class=\"muted small\">· вес ×") + g.w + ("</span>") : '', g.comment ? ("<span class=\"ls-tm\">" + (esc(g.comment)) + "</span>") : '', gradePill(g), icon('next', 18)])).join('')]) : '', avg != null ? fragment("subject", "goal-h", [icon('spark', 20), isParent ? 'Какую оценку за ' + store.periodWord() + ' хотите увидеть?' : 'Какую оценку хочешь за ' + store.periodWord() + '?', goalChat.msgs.map((m) => ("<div class=\"bubble " + (m.me ? 'me' : 'ai') + "\">" + (esc(m.text)) + "</div>")).join(''), quick.map(([c, l]) => ("<button type=\"button\" data-goal=\"" + (c) + "\" class=\"" + (goalChat.msgs.length ? '' : 'primary') + "\">" + (l) + "</button>")).join('')]) : '', childSwitch()]),
    mount(root) {
      bindChildSwitch(root, rerender);
      root.querySelectorAll('[data-stat]').forEach((b) => b.addEventListener('click', () => {
        const g = s.list.find((x) => x.id === b.dataset.stat);
        const st = store.classStats(g);
        const who = isParent ? 'Оценка ребёнка' : 'Твоя оценка';
        const body = st.count < store.STATS_MIN
          ? fragment("subject", "muted", [st.count, st.size, store.STATS_MIN])
          : fragment("subject", "cs-top", [who, gradePill(g, 'big'), zone(st.avg), fmt(st.avg), [5, 4, 3, 2].map((m) => {
               const n = st.buckets[m - 2];
               const me = mark(g.value) === m;
               return fragment("subject", "bar-row", [me ? ' me' : '', m, m, Math.round(n / st.count * 100), n, plural(n, 'ученик', 'ученика', 'учеников'), me ? ("<small>" + (isParent ? 'включая ребёнка' : 'включая тебя') + "</small>") : '']);
             }).join(''), st.count, st.size]);
        sheet(fragment("subject", "row", [icon('close', 18), esc(s.name), esc(g.type), ddmm(g.date), body]));
      }));
      root.querySelectorAll('[data-goal]').forEach((b) => b.addEventListener('click', () => {
        goalChat.msgs.push({ me: true, text: b.textContent }, { me: false, text: answer(b.dataset.goal) });
        rerender();
        const chat = document.getElementById('chat');
        if (chat) chat.lastElementChild.scrollIntoView({ block: 'center' });
      }));
    }
  };
}
export { subjectView };
