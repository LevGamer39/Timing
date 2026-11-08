import { fragment } from './templates.js';
import * as store from './store.js';
import { score, gradeRanges, normalizedBounds } from "./grading.js";
import { esc, icon, mark, fmt, ddmm } from "./ui.js";

import { chat, setChat } from "./shared/teacher.js";

const surname = (n) => n.split(' ')[0];

function analysisView({ params, go, rerender }) {
  const [key, date, slotParam] = params;
  if (!store.journalKeys().some((k) => k.key === key)) { go('journal'); return { html: '' }; }
  const j = store.journal(key);
  const slot = Number(slotParam) || 0;
  const c = j.lessons.findIndex((l) => l.date === date && (Number(l.slot) || 0) === slot);
  if (c < 0) { go('journal/' + key); return { html: '' }; }
  const id = key + date + '#' + slot;
  if (chat.id !== id) setChat({ id, msgs: [] });
  const l = j.lessons[c];
  const cell = store.lessonKey(l);
  const t = store.typeOf(l.type);
  const vals = j.names.map((n, r) => ({ n, v: j.grades[r + '|' + cell] }));
  const nums = vals.filter((x) => typeof x.v === 'number');
  if (!nums.length) return {
    html: '<header class="top"><a class="icon-btn" href="/journal/' + key + '" aria-label="Назад в журнал">' + icon('back') + '</a><h1>Анализ работы</h1></header><div class="card pad empty-state"><p>Оценок по работе пока нет</p><a class="text-action" href="/journal/' + key + '">Открыть журнал</a></div>'
  };
  const avg = nums.length ? nums.reduce((a, x) => a + x.v, 0) / nums.length : 0;

  const bucket = (lo, hi) => nums.filter((x) => score(x.v) >= lo && score(x.v) <= hi);
  const bars = () => {
    const b = gradeRanges().reverse().map((r) => [r.from, r.to, 'g' + r.mark]);
    return b.map(([lo, hi, z]) => {
      const list = bucket(lo, hi);
      const pct = nums.length ? Math.round(list.length / nums.length * 100) : 0;
      return ("<div class=\"bar-row\"><span>" + (lo) + "–" + (hi) + "</span><span class=\"bar\"><i class=\"" + (z) + "\" style=\"width: " + (pct) + "%\"></i></span><span>" + (list.length) + (list.length && list.length <= 2 ? ' · ' + list.map((x) => esc(surname(x.n))).join(', ') : '') + "</span></div>");
    }).join('');
  };
  const missing = vals.filter((x) => x.v === undefined).map((x) => surname(x.n));
  const absent = vals.filter((x) => x.v === 'Н').map((x) => surname(x.n));
  const prevIdx = (() => { for (let i = c - 1; i >= 0; i--) if (j.lessons[i].type === l.type) return i; return -1; })();

  const A = {
    dist: () => ({ html: ("Оценено " + (nums.length) + " из " + (j.names.length) + ", средний <b>" + (fmt(avg)) + "</b>." + (missing.length ? ' Без оценки: ' + esc(missing.map((v) => score(v)).join(', ')) + '.' : '') + (absent.length ? ' Не были: ' + esc(absent.join(', ')) + '.' : '') + "<div class=\"bars\">" + (bars()) + "</div>") }),
    low: () => {
      const low = nums.filter((x) => x.v < normalizedBounds()[0]);
      if (!low.length) return { text: 'Ниже ' + score(normalizedBounds()[0]) + ' никого нет.' };
      return { text: 'Ниже ' + score(normalizedBounds()[0]) + ': ' + low.map((x) => x.n + ' — ' + score(x.v) + ' (средний за ' + store.periodWord() + ' ' + fmt(store.rowAvg(j, j.names.indexOf(x.n))) + ')').join('; ') + '.' };
    },
    cmp: () => {
      if (prevIdx < 0) return { text: 'Это первая работа такого типа в ' + store.periodWord('prep') + ' — сравнивать не с чем.' };
      const pd = j.lessons[prevIdx].date;
      const pk = store.lessonKey(j.lessons[prevIdx]);
      const pn = j.names.map((_, r) => j.grades[r + '|' + pk]).filter((v) => typeof v === 'number');
      const pa = pn.reduce((a, v) => a + v, 0) / (pn.length || 1);
      const up = [], down = [];
      j.names.forEach((n, r) => {
        const a = j.grades[r + '|' + pk], b = j.grades[r + '|' + cell];
        if (typeof a === 'number' && typeof b === 'number') { if (b - a >= 10) up.push(surname(n) + ' +' + (b - a)); if (a - b >= 10) down.push(surname(n) + ' −' + (a - b)); }
      });
      return { text: 'Прошлая такая работа — ' + ddmm(pd) + ', средний ' + fmt(pa) + '. Сейчас ' + fmt(avg) + ' (' + (avg >= pa ? '+' : '−') + fmt(Math.abs(avg - pa)) + ').' + (up.length ? '\nВыросли: ' + up.join(', ') + '.' : '') + (down.length ? '\nУпали: ' + down.join(', ') + '.' : '') };
    },
    quarter: () => {
      const rows = j.names.map((n, r) => ({ n, a: store.rowAvg(j, r) })).filter((x) => x.a != null);
      const cnt = { 5: 0, 4: 0, 3: 0, 2: 0 };
      rows.forEach((x) => cnt[mark(x.a)]++);
      const risk = rows.filter((x) => x.a < normalizedBounds()[0]).map((x) => surname(x.n) + ' ' + fmt(x.a));
      return { text: 'По среднему за ' + store.periodWord() + ' выходит: «5» — ' + cnt[5] + ', «4» — ' + cnt[4] + ', «3» — ' + cnt[3] + ', «2» — ' + cnt[2] + '.' + (risk.length ? '\nНа грани: ' + risk.join(', ') + '.' : '') };
    }
  };
  const QUICK = [['dist', 'Распределение оценок'], ['low', 'Кто ниже ' + score(normalizedBounds()[0])], ['cmp', 'Сравнить с прошлой такой работой'], ['quarter', 'Итоги ' + store.periodWord('gen') + ' по классу']];

  const free = (q) => {
    const s = q.toLowerCase();
    if (/(распредел|сколько|средн)/.test(s)) return A.dist();
    if (/(ниже|двойк|плох|слаб)/.test(s)) return A.low();
    if (/(сравн|прошл|динамик)/.test(s)) return A.cmp();
    if (/(четверт|итог)/.test(s)) return A.quarter();
    if (/(задани|задач|придума|составь)/.test(s)) return { text: 'Задания не предлагаю — это решает учитель. Могу показать статистику по классу и по работам.' };
    const who = j.names.findIndex((n) => s.includes(surname(n).toLowerCase().slice(0, 5)));
    if (who >= 0) {
      const g = j.lessons.map((x) => j.grades[who + '|' + store.lessonKey(x)]).filter((v) => v !== undefined);
      return { text: j.names[who] + ': ' + (g.length ? g.map((v) => score(v)).join(', ') : 'оценок нет') + '. Средний ' + (store.rowAvg(j, who) == null ? '—' : fmt(store.rowAvg(j, who))) + '.' };
    }
    return { text: 'Я считаю статистику по этому журналу: распределение, слабые места, сравнение с прошлой работой, итоги четверти. Можно спросить и про конкретного ученика — по фамилии.' };
  };

  return {
    html: fragment("analysis", "ask", [key, icon('back'), icon('spark', 16), esc(t.name), ddmm(date), esc(j.cls), esc(j.subj), chat.msgs.map((m) => ("<div class=\"bubble " + (m.me ? 'me' : 'ai') + "\"" + (m.html ? ' style="white-space: normal; width: 90%"' : '') + ">" + (m.html || esc(m.text)) + "</div>")).join(''), QUICK.map(([k, label]) => ("<button type=\"button\" data-q=\"" + (k) + "\" class=\"" + (chat.msgs.length ? '' : 'primary') + "\">" + (label) + "</button>")).join(''), icon('send', 20)]),
    mount(root) {
      const push = (q, a) => { chat.msgs.push({ me: true, text: q }, Object.assign({ me: false }, a)); rerender(); window.scrollTo(0, document.body.scrollHeight); };
      root.querySelectorAll('[data-q]').forEach((b) => b.addEventListener('click', () => push(b.textContent, A[b.dataset.q]())));
      root.querySelector('#ask').addEventListener('submit', (e) => { e.preventDefault(); const q = root.querySelector('#q').value.trim(); if (q) push(q, free(q)); });
    }
  };
}
export { analysisView };
