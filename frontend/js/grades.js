import { fragment } from './templates.js';
import * as store from './store.js';
import { esc, icon, gradePill, zone, fmt, mark } from "./ui.js";
import { header, childSwitch, bindChildSwitch } from './common.js';
import { period, periods, setPeriod } from "./shared/grades.js";

function gradesView({ rerender }) {
  const ps = periods();
  const p = ps.find((x) => x.key === period) || store.currentPeriod();
  setPeriod(p.key);
  const list = store.subjects(p.to, p.from);
  return {
    html: fragment("grades", "period", [header(), ps.map((x) => ("<option value=\"" + (x.key) + "\" " + (x.key === period ? 'selected' : '') + ">" + (esc(x.name)) + "</option>")).join(''), childSwitch(), list.map((s) => fragment("grades", "card", [s.key, esc(s.name), s.avg != null ? '<span class="subj-average" aria-label="Средний ' + fmt(s.avg) + ', сейчас выходит ' + mark(s.avg) + '"><span class="g avg ' + zone(s.avg) + '">' + fmt(s.avg) + '</span><span class="muted" aria-hidden="true">→</span><b>' + mark(s.avg) + '</b></span>' : '<span class="muted small">Нет оценок</span>' , icon('next', 20), s.list.length ? ("<span class=\"chips\">" + (s.list.map((g) => gradePill(g)).join('')) + "</span>") : ''])).join('') || '<div class="card pad empty-state"><p>Предметов пока нет</p><a href="/diary">Открыть дневник</a></div>']),
    mount(root, { external = false } = {}) {
      bindChildSwitch(root, rerender);
      root.querySelector('#period').addEventListener('change', (e) => { setPeriod(e.target.value); rerender(); });
      if (!external) store.markSeen();
    }
  };
}
export { gradesView };
