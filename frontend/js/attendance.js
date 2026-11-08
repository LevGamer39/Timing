import { fragment } from './templates.js';
import * as store from './store.js';
import { esc } from "./ui.js";
import { header } from './common.js';
import { absenceBlock, adminCls, setAdminCls } from "./shared/attendance.js";

function attendanceView({ rerender }) {
  const all = store.classes();
  if (adminCls !== 'all' && !all[adminCls]) setAdminCls('all');
  const keys = Object.keys(all);
  if (!keys.length) return { html: header() + '<h1 class="title-l">Пропуски</h1><div class="card pad empty-state"><p>Классов пока нет</p><a class="text-action" href="/classes">Открыть классы</a></div>' };
  const block = absenceBlock(adminCls === 'all' ? keys : [adminCls], { keys, pick: (k) => { setAdminCls(k); rerender(); } });
  return {
    html: fragment("attendance", "stack", [header(), adminCls === 'all' ? 'Пропуски' : 'Пропуски · ' + esc(all[adminCls].name), block.html]),
    mount(root) {
      block.mount(root, rerender);
    }
  };
}
export { attendanceView };
