import { fragment } from './templates.js';
import * as store from './store.js';
import { esc, icon, toast, sheet } from './ui.js';
import { header } from './common.js';

const PRIORITY = { high: 'Высокая', medium: 'Обычная', low: 'Низкая' };

const subjectOptions = () => store.subjects()
  .map((s) => '<option value="' + esc(s.name) + '">' + esc(s.name) + '</option>')
  .join('');

const dateLabel = (value) => {
  if (!value) return 'Без срока';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return 'Без срока';
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' }) + ', ' + d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
};

const overdue = (t) => t.deadline && t.status !== 'done' && new Date(t.deadline).getTime() < Date.now();

function taskCard(t, i) {
  return '<article class="card pad task-card' + (t.status === 'done' ? ' done' : '') + '" data-task="' + i + '">'
    + '<label class="row" style="gap: 10px; align-items: flex-start; min-height: 28px">'
    + '<input type="checkbox" data-done="' + i + '" ' + (t.status === 'done' ? 'checked' : '') + ' aria-label="Готово">'
    + '<span class="grow"><b class="task-title">' + esc(t.title) + '</b>'
    + (t.subject ? '<br><span class="muted small">' + esc(t.subject) + '</span>' : '')
    + '</span>'
    + '<span class="task-prio prio-' + esc(t.priority || 'medium') + '">' + (PRIORITY[t.priority] || 'Обычная') + '</span>'
    + '</label>'
    + '<p class="hint task-date' + (overdue(t) ? ' late' : '') + '">' + (overdue(t) ? 'Просрочено: ' : 'Срок: ') + dateLabel(t.deadline) + '</p>'
    + (t.description ? '<p class="task-desc">' + esc(t.description) + '</p>' : '')
    + '<div class="row" style="gap: 6px; justify-content: flex-end">'
    + '<button class="text-action" type="button" data-sub="' + i + '">Подзадачи</button>'
    + '<button class="text-action" type="button" data-ai="' + i + '">ИИ-план</button>'
    + '<button class="icon-btn" type="button" data-del="' + i + '" aria-label="Удалить задачу">' + icon('trash', 18) + '</button>'
    + '</div>'
    + '</article>';
}

function formSheet(task, onSaved) {
  sheet(fragment('tasks', 'form', [task ? 'Изменить задачу' : 'Новая задача', subjectOptions(), task ? 'Сохранить' : 'Добавить']), (el, close) => {
    const err = el.querySelector('#tf-err');
    const fail = (text) => { err.textContent = text; err.hidden = false; };
    if (task) {
      el.querySelector('#tf-title').value = task.title || '';
      el.querySelector('#tf-subject').value = task.subject || '';
      el.querySelector('#tf-priority').value = task.priority || 'medium';
      el.querySelector('#tf-desc').value = task.description || '';
      if (task.deadline) el.querySelector('#tf-deadline').value = new Date(task.deadline).toISOString().slice(0, 16);
    }
    el.querySelector('#task-f').addEventListener('submit', (e) => {
      e.preventDefault();
      const title = el.querySelector('#tf-title').value.trim();
      if (!title) return fail('Введите, что нужно сделать.');
      const raw = el.querySelector('#tf-deadline').value;
      const body = {
        title,
        subject: el.querySelector('#tf-subject').value || null,
        description: el.querySelector('#tf-desc').value.trim() || null,
        deadline: raw ? new Date(raw).toISOString() : null,
        priority: el.querySelector('#tf-priority').value
      };
      const run = task ? store.taskUpdate(task.id, body) : store.taskCreate(body);
      run.then((result) => {
        if (typeof result === 'string') return fail(result);
        close(); onSaved();
      }).catch((error) => fail((error && error.message) || store.SAVE_ERROR));
    });
  });
}

function subtaskSheet(task, onSaved) {
  const draw = (el, items) => {
    const box = el.querySelector('#sub-list');
    box.innerHTML = items.length
      ? items.map((s) => fragment('tasks', 'sub', [s.id, s.status === 'done' ? 'checked' : '', s.status === 'done' ? 'text-decoration: line-through; opacity: .6' : '', esc(s.title)])).join('')
      : '<p class="muted" style="margin: 0">Подзадач пока нет. Разбейте дело на шаги или попросите ИИ.</p>';
    box.querySelectorAll('[data-subdone]').forEach((cb) => cb.addEventListener('change', () => {
      store.taskUpdate(Number(cb.dataset.subdone), { status: cb.checked ? 'done' : 'pending' })
        .then(() => load()).catch(() => { cb.checked = !cb.checked; toast(store.SAVE_ERROR); });
    }));
  };
  const load = () => store.taskSubtasks(task.id).then((items) => draw(el, Array.isArray(items) ? items : [])).catch(() => draw(el, []));
  let el;
  sheet('<h2 class="h2" style="font-size: 20px">Подзадачи</h2><p class="hint">' + esc(task.title) + '</p>'
    + '<div id="sub-list" class="stack-sm"><p class="muted" style="margin: 0">Загружаю…</p></div>'
    + '<form class="stack-sm" id="sub-f" style="margin-top: 12px"><input class="input" id="sub-in" maxlength="200" placeholder="Новый шаг"><button class="btn sm" type="submit" style="align-self: flex-start">Добавить шаг</button></form>', (node, close) => {
    el = node;
    node.querySelector('#sub-f').addEventListener('submit', (e) => {
      e.preventDefault();
      const input = node.querySelector('#sub-in');
      const title = input.value.trim();
      if (!title) return;
      store.taskCreate({ title, parent_task_id: task.id }).then(() => { input.value = ''; load(); onSaved(); })
        .catch(() => toast(store.SAVE_ERROR));
    });
    load();
  });
}

function tasksView({ rerender }) {
  let items = [];
  let loaded = false;
  const isServer = store.isServer();

  const mount = (root) => {
    const box = root.querySelector('#task-list');
    const draw = () => {
      if (!isServer) {
        box.innerHTML = '<div class="card pad"><p class="muted" style="margin: 0">Личные задачи доступны после входа в аккаунт. В демо-режиме задания смотрите в дневнике.</p></div>';
        return;
      }
      const open = items.filter((t) => t.status !== 'done');
      const done = items.filter((t) => t.status === 'done');
      box.innerHTML = (open.length ? open.map(taskCard).join('') : '<div class="card pad"><p class="muted" style="margin: 0">Активных задач нет. Добавьте первую — она появится здесь и в дневнике.</p></div>')
        + (done.length ? '<h2 class="h2" style="font-size: 18px; margin: 8px 0 0">Выполнено</h2>' + done.map(taskCard).join('') : '');
      box.querySelectorAll('[data-done]').forEach((cb) => cb.addEventListener('change', () => {
        const task = items[Number(cb.dataset.done)];
        const run = cb.checked ? store.taskComplete(task.id) : store.taskUpdate(task.id, { status: 'pending' });
        run.then((result) => {
          if (typeof result === 'string') { cb.checked = !cb.checked; return toast(result); }
          reload();
        }).catch(() => { cb.checked = !cb.checked; toast(store.SAVE_ERROR); });
      }));
      box.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', () => {
        const task = items[Number(b.dataset.del)];
        store.taskDelete(task.id).then((result) => {
          if (typeof result === 'string') return toast(result);
          toast('Задача удалена'); reload();
        }).catch(() => toast(store.SAVE_ERROR));
      }));
      box.querySelectorAll('[data-sub]').forEach((b) => b.addEventListener('click', () => subtaskSheet(items[Number(b.dataset.sub)], reload)));
      box.querySelectorAll('[data-ai]').forEach((b) => b.addEventListener('click', () => {
        const task = items[Number(b.dataset.ai)];
        b.disabled = true;
        store.taskAiSubtasks(task.id).then((result) => {
          if (typeof result === 'string') return toast(result);
          if (result && result.fallback) toast('ИИ недоступен, шаги собраны по шаблону');
          subtaskSheet(task, reload);
        }).catch(() => toast('Не удалось получить план')).finally(() => { b.disabled = false; });
      }));
    };
    const reload = () => {
      if (!isServer) { draw(); return; }
      store.taskList().then((list) => { items = Array.isArray(list) ? list : []; loaded = true; draw(); })
        .catch(() => { box.innerHTML = '<div class="card pad"><p class="muted" style="margin: 0">Не удалось загрузить задачи.</p></div>'; });
    };
    root.querySelector('#task-add')?.addEventListener('click', () => {
      if (!isServer) return toast('Добавление задач доступно после входа.');
      formSheet(null, reload);
    });
    reload();
  };

  return {
    html: fragment('tasks', 'screen', [header(), icon('plus', 18)]),
    mount
  };
}

export { tasksView };
