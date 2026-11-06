import { fragment } from './templates.js';
import * as store from './store.js';
import { ACCENTS } from './theme.js';
import { esc, icon, toast, AVATARS, avatarSvg } from './ui.js';
import { copyCode } from './clipboard.js';

const ROLE_NAME = { student: 'Ученик', parent: 'Родитель', teacher: 'Учитель', admin: 'Администратор' };

const SCALES = [5, 10, 12, 20, 100];

const PERIOD_TYPES = [['quarters', 'Четверти'], ['trimesters', 'Триместры'], ['halves', 'Полугодия']];

function settingsView({ go, rerender }) {
  const acc = store.account();
  const role = store.role();
  const theme = store.get().theme;
  const login = store.get().session.login;
  const av = store.avatarOf();
  const server = store.isServer();
  const sch = role === 'admin' ? store.school() : null;
  const opt = (v, label, cur, attr) => ("<button type=\"button\" " + (attr) + "=\"" + (v) + "\" aria-pressed=\"" + (cur === v) + "\">" + (label) + "</button>");
  const sub = acc.title || (role === 'parent' ? 'Родитель' : role === 'student' ? 'Ученик · ' + store.student().cls + ' класс' : ROLE_NAME[role]);
  const bounds = sch ? store.boundsFor(sch, sch.scale) : null;

  const left = fragment("settings", "card", [!av, esc(acc.initials), AVATARS.map((a) => ("<button type=\"button\" class=\"av-opt\" data-av=\"" + (a.id) + "\" aria-pressed=\"" + (av === a.id) + "\" aria-label=\"Аватарка " + (a.id) + "\">" + (avatarSvg(a.id, 48)) + "</button>")).join(''), role === 'parent' ? fragment("settings", "card-2", [store.children().map((c) => fragment("settings", "list-row", [c.id, esc(c.initials), esc(c.first), esc(c.cls), c.id === store.student().id ? ("<span class=\"pill\">Выбран</span>") : ("<span class=\"muted small\" style=\"font-weight: 700\">Выбрать</span>")])).join('')]) : '']);

  const right = fragment("settings", "access", [esc(login), icon('logout', 18)])
    + (server ? fragment("settings", "profile", [esc(acc.first || ''), esc(acc.last || '')]) + fragment("settings", "password", []) : '')
    + (server ? fragment("settings", "email", [esc(acc.email || 'не задан')]) : '')
    + (server ? fragment("settings", "notify", [acc.notifyEmail ? 'checked' : '', acc.notifyBrowser ? 'checked' : '', esc(acc.digestTime || '19:00'), acc.notifyMax ? 'checked' : '']) : '')
    + (server && role === 'student' ? fragment("settings", "join-class", []) : '')
    + (server && role === 'student' ? fragment("settings", "parent-code", []) : '')
    + (server && role === 'parent' ? fragment("settings", "parent-link", []) : '');

  const admin = sch ? fragment("settings", "zf", [SCALES.map((n) => opt(n, String(n), sch.scale, 'data-scale')).join(''), [2, 3, 4, 5].map((m, i) => {
        const from = i === 0 ? (sch.scale === 5 ? 1 : 0) : bounds[i - 1];
        const to = i === 3 ? sch.scale : bounds[i] - 1;
        const inp = (v, attr, label) => ("<input class=\"zone-in\" type=\"number\" min=\"0\" max=\"" + (sch.scale) + "\" value=\"" + (v) + "\" " + (attr) + " aria-label=\"" + (label) + "\">");
        return ("<div class=\"zone g" + (m) + "\">«" + (m) + "»<span>от " + (i === 0 ? from : inp(from, 'data-b="' + (i - 1) + '"', 'Оценка ' + m + ' — от')) + " до " + (i === 3 ? to : inp(to, 'data-to="' + i + '"', 'Оценка ' + m + ' — до')) + "</span></div>");
      }).join(''), PERIOD_TYPES.map(([v, l]) => opt(v, l, sch.periodType, 'data-ptype')).join(''), sch.periods[sch.periodType].map(([name, from, to], i) => fragment("settings", "pr", [/каникул/i.test(name) ? '; color: var(--muted)' : '', esc(name), from, i, esc(name), to, i, esc(name)])).join('')]) : '';

  return {
    html: fragment("settings", "screen", [icon('back'), av ? '; background: none' : '', av ? avatarSvg(av, 64) : esc(acc.initials), esc(acc.name), esc(sub), esc(store.SCHOOL.name), esc(login), opt('light', 'Светлая', theme, 'data-theme-opt'), opt('dark', 'Тёмная', theme, 'data-theme-opt'), opt('system', 'Авто', theme, 'data-theme-opt'), ACCENTS.map((p) => ("<button class=\"accent-option\" type=\"button\" data-accent=\"" + (p.id) + "\" aria-pressed=\"" + ((store.get().accent || 'orange') === p.id) + "\"><span style=\"background: " + (p.light) + "\" aria-hidden=\"true\"></span>" + (p.name) + "</button>")).join(''), left, right, admin]),
    mount(root) {
      root.querySelectorAll('[data-theme-opt]').forEach((b) => b.addEventListener('click', () => { store.set({ theme: b.dataset.themeOpt }); rerender(); }));
      root.querySelectorAll('[data-av]').forEach((b) => b.addEventListener('click', () => { if (!store.setAvatar(b.dataset.av)) return toast(store.SAVE_ERROR); rerender(); toast('Аватарка сохранена'); }));
      root.querySelectorAll('[data-child]').forEach((b) => b.addEventListener('click', () => { if (!store.setChild(b.dataset.child)) return toast(store.SAVE_ERROR); rerender(); toast('Показываю дневник: ' + store.student().first); }));
      root.querySelector('#out').addEventListener('click', () => { store.logout(); go('login'); });

      const nameForm = root.querySelector('#name-f');
      if (nameForm) nameForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const err = root.querySelector('#pf-err');
        const first = root.querySelector('#pf-first').value.trim();
        const last = root.querySelector('#pf-last').value.trim();
        if (!first || !last) { err.textContent = 'Имя и фамилия не должны быть пустыми.'; err.hidden = false; return; }
        err.hidden = true;
        if (!store.updateProfile({ first_name: first, last_name: last })) return toast(store.SAVE_ERROR);
        rerender(); toast('Имя сохранено');
      });
      const codeBtn = root.querySelector('#pl-code');
      if (codeBtn) codeBtn.addEventListener('click', () => {
        const err = root.querySelector('#pl-err');
        err.hidden = true;
        store.parentLinkCode().then((result) => {
          if (result.error) { err.textContent = result.error; err.hidden = false; return; }
          root.querySelector('#pl-slot').innerHTML = '<p class="mono" style="font-size: 22px; font-weight: 800; letter-spacing: 2px; margin: 8px 0">' + esc(result.code) + '</p>' + copyCode(result.code, 'код родителя');
        });
      });
      const linkForm = root.querySelector('#pl-form');
      if (linkForm) linkForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const err = root.querySelector('#pl-err');
        const code = root.querySelector('#pl-in').value.trim();
        if (!/^\d{8}$/.test(code)) { err.textContent = 'Код — 8 цифр.'; err.hidden = false; return; }
        err.hidden = true;
        store.linkParent(code).then((message) => {
          if (message) { err.textContent = message; err.hidden = false; return; }
          linkForm.reset(); rerender(); toast('Ребёнок привязан');
        });
      });
      const emailForm = root.querySelector('#em-f');
      if (emailForm) emailForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const err = root.querySelector('#em-err');
        const value = root.querySelector('#em-in').value.trim();
        if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value)) { err.textContent = 'Введите корректный email.'; err.hidden = false; return; }
        err.hidden = true;
        store.changeEmail(value).then((message) => {
          if (message) { err.textContent = message; err.hidden = false; return; }
          emailForm.reset(); toast('Письмо с подтверждением отправлено на ' + value);
        });
      });
      root.querySelector('#nt-save')?.addEventListener('click', () => {
        const err = root.querySelector('#nt-err');
        const patch = {
          notify_email: root.querySelector('#nt-email').checked,
          notify_browser: root.querySelector('#nt-browser').checked,
          notify_max: root.querySelector('#nt-max').checked,
          notify_digest_time: root.querySelector('#nt-time').value || '19:00'
        };
        if (!store.updateProfile(patch)) { err.textContent = store.SAVE_ERROR; err.hidden = false; return; }
        toast('Уведомления сохранены');
      });
      const joinForm = root.querySelector('#jc-f');
      if (joinForm) joinForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const err = root.querySelector('#jc-err');
        const code = root.querySelector('#jc-in').value.trim().toUpperCase();
        if (!/^[A-Z0-9]{12}$/.test(code)) { err.textContent = 'Код класса — 12 символов.'; err.hidden = false; return; }
        err.hidden = true;
        store.joinClass(code).then((message) => {
          if (message) { err.textContent = message; err.hidden = false; return; }
          joinForm.reset(); rerender(); toast('Вы в классе');
        });
      });
      const pwForm = root.querySelector('#pw-f');
      if (pwForm) pwForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const err = root.querySelector('#pw-err');
        const cur = root.querySelector('#pw-cur').value;
        const next = root.querySelector('#pw-new').value;
        const rep = root.querySelector('#pw-rep').value;
        const show = (text) => { err.textContent = text; err.hidden = false; };
        if (!cur) return show('Введите текущий пароль.');
        if (next.length < 8) return show('Новый пароль должен быть не короче 8 символов.');
        if (next !== rep) return show('Новый пароль и повтор не совпадают.');
        err.hidden = true;
        store.changePassword(cur, next).then((message) => {
          if (message) return show(message);
          pwForm.reset(); toast('Пароль изменён');
        });
      });

      root.querySelectorAll('[data-accent]').forEach((b) => b.addEventListener('click', () => { store.set({ accent: b.dataset.accent }); rerender(); }));
      if (!sch) return;
      const draft = sch.bellSets, days = sch.bellDays;
      const readBells = () => {
        root.querySelectorAll('[data-bell-set]').forEach((el, i) => {
          draft[i].name = el.querySelector('[data-set-name]').value;
          draft[i].bells = [...el.querySelectorAll('[data-bell-row]')].map((r) => ({ start: r.querySelector('[data-start]').value, end: r.querySelector('[data-end]').value }));
        });
        root.querySelectorAll('[data-bell-day]').forEach((el) => { days[Number(el.dataset.bellDay)] = el.value; });
      };
      const drawBells = () => {
        root.querySelector('#bells-list').innerHTML = draft.map((b, i) => '<section class="bell-set stack-sm" data-bell-set><label class="label" for="bell-name-' + i + '">Название набора</label><input class="input" id="bell-name-' + i + '" data-set-name maxlength="60" value="' + esc(b.name) + '"><div><div class="bell-row muted small" aria-hidden="true"><span>№</span><span>Начало</span><span>Конец</span></div>' + b.bells.map((l, n) => fragment('settings', 'bell-row', [n + 1, esc(l.start), n + 1, esc(l.end), n + 1, n === b.bells.length - 1 ? '<button class="icon-btn" type="button" data-bell-remove="' + i + '" aria-label="Убрать последний урок: ' + esc(b.name) + '">' + icon('trash', 18) + '</button>' : ''])).join('') + '</div><button class="btn ghost sm" data-bell-add="' + i + '" type="button">+ Урок</button></section>').join('');
        root.querySelector('#bell-days').innerHTML = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'].map((d, i) => '<label class="bell-day"><span class="label">' + d + '</span><select class="input" data-bell-day="' + i + '" aria-label="Набор звонков: ' + d + '">' + draft.map((b) => '<option value="' + esc(b.id) + '"' + (days[i] === b.id ? ' selected' : '') + '>' + esc(b.name) + '</option>').join('') + '</select></label>').join('');
      };
      drawBells();
      root.querySelector('#bells-list').addEventListener('input', (e) => {
        if (!e.target.matches('[data-set-name]')) return;
        readBells();
        root.querySelectorAll('[data-bell-day]').forEach((select) => {
          [...select.options].forEach((option) => { option.textContent = draft.find((b) => b.id === option.value)?.name || 'Без названия'; });
        });
      });
      root.querySelector('#bells-list').addEventListener('click', (e) => {
        const remove = e.target.closest('[data-bell-remove]'), add = e.target.closest('[data-bell-add]');
        if (!remove && !add) return;
        readBells();
        const list = draft[Number((remove || add).dataset[remove ? 'bellRemove' : 'bellAdd'])].bells;
        if (remove) list.pop();
        else {
          const last = (list.at(-1)?.end || '07:40').split(':').map(Number), start = last[0] * 60 + last[1] + 10;
          if (!Number.isFinite(start) || start + 40 >= 1440) return toast('Проверьте время последнего урока');
          const time = (v) => String(Math.floor(v / 60)).padStart(2, '0') + ':' + String(v % 60).padStart(2, '0');
          list.push({ start: time(start), end: time(start + 40) });
        }
        drawBells();
      });
      root.querySelector('#bell-set-add').addEventListener('click', () => {
        readBells(); draft.push({ id: crypto.randomUUID(), name: 'Новый набор', bells: draft[0].bells.map((b) => ({ ...b })) }); drawBells();
      });
      root.querySelector('#bells-f').addEventListener('submit', (e) => {
        e.preventDefault(); readBells(); sch.bells = draft[0].bells;
        const message = store.bellSetsError(sch), error = root.querySelector('#bells-error'); error.hidden = !message; error.textContent = message;
        root.querySelectorAll('.bell-row-error').forEach((el) => el.remove());
        root.querySelectorAll('#bells-list [aria-invalid]').forEach((el) => { el.removeAttribute('aria-invalid'); el.removeAttribute('aria-describedby'); });
        for (let i = 0; i < draft.length; i++) {
          const issue = store.bellTimeIssue(draft[i].bells);
          if (!issue) continue;
          const rows = root.querySelectorAll('[data-bell-set]')[i].querySelectorAll('[data-bell-row]');
          const row = rows[issue.row], input = row.querySelector('[data-' + issue.field + ']');
          const notice = document.createElement('p');
          notice.id = 'bell-error-' + i + '-' + issue.row; notice.className = 'error bell-row-error'; notice.setAttribute('role', 'alert'); notice.textContent = draft[i].name + ': ' + issue.message;
          row.append(notice);
          const mark = (field) => { field.setAttribute('aria-invalid', 'true'); field.setAttribute('aria-describedby', notice.id); };
          mark(input);
          if (issue.overlap) mark(rows[issue.row - 1].querySelector('[data-end]'));
          error.hidden = true;
          input.focus({ preventScroll: true }); row.scrollIntoView?.({ block: 'center' });
          return;
        }
        if (!message) { const error = store.saveSchool(sch); if (error) return toast(error); drawBells(); toast('Звонки сохранены'); }
      });
      root.querySelectorAll('[data-scale]').forEach((b) => b.addEventListener('click', () => { sch.scale = Number(b.dataset.scale); const error = store.saveSchool(sch); if (error) return toast(error); rerender(); toast('Шкала: ' + sch.scale + ' баллов'); }));
      // «до» одной оценки = «от» следующей − 1.
      root.querySelectorAll('[data-to]').forEach((t) => t.addEventListener('input', () => {
        const next = root.querySelector('[data-b="' + t.dataset.to + '"]');
        if (next && t.value !== '') next.value = Number(t.value) + 1;
      }));
      root.querySelectorAll('[data-b]').forEach((f) => f.addEventListener('input', () => {
        const prev = root.querySelector('[data-to="' + f.dataset.b + '"]');
        if (prev && f.value !== '') prev.value = Number(f.value) - 1;
      }));
      root.querySelector('#zf').addEventListener('submit', (e) => {
        e.preventDefault();
        const err = root.querySelector('#z-err');
        const b = [...root.querySelectorAll('[data-b]')].map((i) => Number(i.value));
        if (b.some((v) => !Number.isInteger(v) || v < 1 || v > sch.scale) || !(b[0] < b[1] && b[1] < b[2])) {
          err.textContent = 'Границы должны идти по возрастанию: «3» < «4» < «5», от 1 до ' + sch.scale + '.'; err.hidden = false; return;
        }
        sch.bounds[sch.scale] = b; const error = store.saveSchool(sch); if (error) return toast(error); rerender(); toast('Границы оценок сохранены');
      });
      root.querySelector('#z-reset').addEventListener('click', () => { delete sch.bounds[sch.scale]; const error = store.saveSchool(sch); if (error) return toast(error); rerender(); toast('Границы по умолчанию'); });
      root.querySelectorAll('[data-ptype]').forEach((b) => b.addEventListener('click', () => { sch.periodType = b.dataset.ptype; const error = store.saveSchool(sch); if (error) return toast(error); rerender(); }));
      root.querySelector('#per-f').addEventListener('submit', (e) => {
        e.preventDefault();
        const err = root.querySelector('#per-err');
        const list = sch.periods[sch.periodType].map((p) => p.slice());
        e.target.querySelectorAll('input[type=date]').forEach((i) => { list[i.dataset.i][i.dataset.k] = i.value; });
        const bad = list.findIndex(([, a, b], i) => !a || !b || a > b || (i && list[i - 1][2] >= a));
        if (bad >= 0) { err.textContent = 'Проверьте даты: «' + list[bad][0] + '» — начало раньше конца и после предыдущего периода.'; err.hidden = false; return; }
        sch.periods[sch.periodType] = list; const error = store.saveSchool(sch); if (error) return toast(error); toast('Периоды сохранены');
      });
    }
  };
}
export { settingsView };
