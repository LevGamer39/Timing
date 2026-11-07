import { fragment } from './templates.js';
import * as store from './store.js';
import { score } from './grading.js';
import { esc, icon, fmt, mark, goalText, plural, dayMonth, iso, DAYS, weekday, toast } from './ui.js';
import { chat, topic, chats, chatId, chatsLoaded, setChat, setTopic, setChats, setChatId } from "./shared/ai.js";

// Какой чат уже запрошен: при пустой истории повторный запрос после rerender
// зациклил бы экран.
let loadedChat = null;

function summary() {
  const subs = store.subjects().filter((s) => s.avg != null);
  if (!subs.length) return 'Оценок пока нет.';
  const total = subs.reduce((a, s) => a + s.avg, 0) / subs.length;
  const low = subs.slice().sort((a, b) => a.avg - b.avg).slice(0, 2);
  const counts = { 5: 0, 4: 0, 3: 0, 2: 0 };
  subs.forEach((s) => counts[mark(s.avg)]++);
  return 'Средний по всем предметам — ' + fmt(total) + '.\n' +
    'Пятёрки выходят по ' + counts[5] + ', четвёрки по ' + counts[4] + ', тройки по ' + counts[3] + (counts[2] ? ', двойки по ' + counts[2] : '') + '.\n' +
    'Ниже всего: ' + low.map((s) => s.name.toLowerCase() + ' — ' + fmt(s.avg)).join(', ') + '.';
}

function weekReport() {
  const w = store.week(0);
  const grades = w.days.flatMap((d) => (d.lessons || []).flatMap((l) => l.grades));
  const absent = w.days.flatMap((d) => (d.lessons || []).filter((l) => l.absent).map((l) => l.name.toLowerCase()));
  const hw = w.days.flatMap((d) => (d.lessons || []).filter((l) => l.hw));
  if (!grades.length) return 'На этой неделе оценок пока нет.';
  const avg = grades.reduce((a, g) => a + g.value, 0) / grades.length;
  const best = grades.reduce((a, g) => (g.value > a.value ? g : a));
  const worst = grades.reduce((a, g) => (g.value < a.value ? g : a));
  const name = (g) => store.SUBJECTS[g.subj].name.toLowerCase();
  return 'Эта неделя: ' + grades.length + ' ' + plural(grades.length, 'оценка', 'оценки', 'оценок') + ', средняя ' + fmt(avg) + '.\n' +
    'Лучшая — ' + name(best) + ' ' + score(best.value) + ', самая низкая — ' + name(worst) + ' ' + score(worst.value) + '.\n' +
    (absent.length ? 'Пропуски: ' + absent.join(', ') + '.\n' : 'Пропусков нет.\n') +
    (store.role() === 'student' ? 'Домашка: отмечено сделанным ' + hw.filter((l) => l.done).length + ' из ' + hw.length + '.' : 'Домашних заданий на неделе: ' + hw.length + '.');
}

function holidays() {
  const h = store.nextHolidays();
  if (!h) return 'В этом учебном году каникул больше нет.';
  const range = dayMonth(store.parse(h.from)) + ' – ' + dayMonth(store.parse(h.to));
  if (h.now) return 'Сейчас ' + h.name.toLowerCase() + ': ' + range + '. Отдыхай!';
  const s = store.school();
  const after = s.periods[s.periodType].filter(([n, from]) => /каникул/i.test(n) && from > h.from).slice(0, 2);
  return h.name + ' через ' + h.days + ' ' + plural(h.days, 'день', 'дня', 'дней') + ': ' + range + '.' +
    (after.length ? '\nДальше: ' + after.map(([n, from, to]) => n.toLowerCase() + ' ' + dayMonth(store.parse(from)) + ' – ' + dayMonth(store.parse(to))).join(', ') + '.' : '');
}

function periodEnd() {
  const s = store.school();
  const today = iso(store.NOW);
  const p = s.periods[s.periodType].find(([n, from, to]) => !/каникул/i.test(n) && from <= today && to >= today);
  if (!p) return 'Сейчас не идёт учебный период — каникулы.';
  const days = store.periodDaysLeft();
  return p[0] + ' закончится через ' + days + ' ' + plural(days, 'день', 'дня', 'дней') + ', последний учебный день — ' + dayMonth(store.parse(p[2])) + '. Итоговые оценки ставят учителя до этой даты.';
}

const HELP = {
  'Как поставить цель': 'Оценки → нажми на предмет → внизу блок «Цель». Выбери «Хочу 5» — посчитаю, какие оценки нужны.',
  'Как написать учителю': 'Почта → «Написать» → начни вводить фамилию. Под именем видно, какой предмет ведёт учитель. Можно прикрепить файл или фото.',
  'Где старые оценки': 'Оценки → справа сверху выбери период: четверть или учебный год.',
  'Как сменить тему': 'Шестерёнка «Настройки» справа сверху → «Тема»: светлая, тёмная или «Авто».'
};

function topics() {
  const subs = store.subjects().filter((s) => s.avg != null).sort((a, b) => a.avg - b.avg).slice(0, 4);
  return {
    grades: { title: 'Мои оценки', desc: 'Средний и где проседаю', ask: 'Что посмотреть?', subs: { 'Общая сводка': summary, 'Что за эту неделю': weekReport } },
    school: { title: 'Каникулы и четверть', desc: 'Когда отдых и конец четверти', ask: 'Что подсказать?', subs: { 'Когда каникулы': holidays, 'Когда конец четверти': periodEnd } },
    goal: { title: 'Цель по предмету', desc: 'Сколько нужно до 4 или 5', ask: 'По какому предмету? Показываю те, где средний ниже всего.', subs: Object.fromEntries(subs.map((s) => [s.name, () => goalText(s.list, Math.min(mark(s.avg) + 1, 5))])) },
    help: { title: 'Как пользоваться', desc: 'Где что находится', ask: 'Что подсказать?', subs: Object.fromEntries(Object.entries(HELP).map(([k, v]) => [k, () => v])) }
  };
}

function scheduleReport(q) {
  const names = ['понедельник', 'вторник', 'сред', 'четверг', 'пятниц', 'суббот', 'воскресень'];
  const picked = names.findIndex((name) => q.includes(name));
  const day = picked >= 0 ? picked : q.includes('завтра') ? (weekday(store.NOW) + 1) % 7 : q.includes('сегодня') ? weekday(store.NOW) : null;
  return store.week(0).days.filter((_, i) => day === null || i === day).map((d) => DAYS[weekday(d.d)] + ':\n' + (d.lessons?.length ? d.lessons.map((l) => l.n + '. ' + l.name + ' · ' + l.time).join('\n') : d.off)).join('\n\n');
}

function freeAnswer(q) {
  const t = q.toLowerCase();
  if (/(реши|решени|ответ|сочинени|задач|упражн|переведи|домашк)/.test(t)) return 'С домашкой не помогаю — это было бы списывание. Могу показать, что задано, или посчитать, сколько нужно до нужной оценки.';
  if (/(каникул|отдых|отдыха)/.test(t)) return holidays();
  if (/(конец четверт|четверть законч|итогов)/.test(t)) return periodEnd();
  if (/(расписан|звонк|время урок|когда урок|начина.тся урок)/.test(t)) return scheduleReport(t);
  if (/(неделя|недел)/.test(t)) return weekReport();
  if (/(средн|оценк|балл|проседа)/.test(t)) return summary();
  const subj = store.subjects().find((s) => t.includes(s.name.toLowerCase().slice(0, 5)));
  if (subj && subj.avg != null) return goalText(subj.list, Math.min(mark(subj.avg) + 1, 5));
  if (/(напис|учител|почт)/.test(t)) return HELP['Как написать учителю'];
  if (/(тем|тёмн|темн|светл)/.test(t)) return HELP['Как сменить тему'];
  return 'Я отвечаю про оценки, цели, неделю и про то, как пользоваться Таймингом. Попробуй, например: «Где я проседаю?», «Что нужно для 5 по химии?» или «Когда каникулы?»';
}

const genitive = (n) => /[гкхжшчщ]а$/.test(n) ? n.slice(0, -1) + 'и' : /а$/.test(n) ? n.slice(0, -1) + 'ы'
  : /[яь]$/.test(n) ? n.slice(0, -1) + 'и' : /й$/.test(n) ? n.slice(0, -1) + 'я' : n + 'а';

function messageHtml(m, index) {
  const bubble = '<div class="bubble ' + (m.me ? 'me' : 'ai') + '">' + esc(m.text) + '</div>';
  if (!m.pending) return bubble;
  const label = m.pending.operation === 'delete' ? 'Удалить' : 'Добавить';
  return bubble + '<button class="confirm" type="button" data-confirm="' + index + '">' + label + '</button>';
}

function chatStrip(isAi) {
  if (!isAi) return '';
  return '<div class="chats" aria-label="Мои чаты">' +
    '<button type="button" class="chat-chip new" data-new>+ Новый чат</button>' +
    chats.map((c) => '<button type="button" class="chat-chip"' + (c.id === chatId ? ' aria-current="true"' : '') + ' data-chat="' + c.id + '">' + esc(c.title) + '</button>').join('') +
    (chatId ? '<button type="button" class="chat-chip del" data-del="' + chatId + '" aria-label="Удалить чат">×</button>' : '') +
    '</div>';
}

function aiView({ rerender }) {
  const T = topics();
  const isParent = store.role() === 'parent';
  const isAi = store.isServer() && store.role() === 'student';
  const s = store.student();

  const hello = isParent
    ? 'Здравствуйте! Подскажу по оценкам ' + genitive(s.first) + ' и по самому Таймингу. С чего начнём?'
    : 'Привет, ' + s.first + '! Помогаю разобраться с оценками и с Таймингом. Домашку за тебя не решаю. С чего начнём?';

  return {
    html: fragment("ai", "screen", [icon('back'), icon('spark', 20), icon('reset', 20), Object.entries(T).map(([k, v]) => ("<button class=\"cat\" type=\"button\" data-topic=\"" + (k) + "\" style=\"min-height: 0\"" + (topic === k ? ' aria-current="true"' : '') + "><b>" + (esc(v.title)) + "</b><span>" + (esc(v.desc)) + "</span></button>")).join(''), isParent ? ' ребёнка' : '', esc(hello), chat.map((m, i) => messageHtml(m, i)).join(''), topic ? ("\n      <div class=\"quick\">" + (Object.keys(T[topic].subs).map((k) => ("<button type=\"button\" data-sub=\"" + (esc(k)) + "\">" + (esc(k)) + "</button>")).join('')) + "<button type=\"button\" data-topic=\"\">Другая тема</button></div>") : chat.length ? ("\n      <div class=\"quick only-mobile\">" + (Object.entries(T).map(([k, v]) => ("<button type=\"button\" data-topic=\"" + (k) + "\">" + (esc(v.title)) + "</button>")).join('')) + "</div>") : ("\n      <div class=\"cats\">" + (Object.entries(T).map(([k, v]) => ("<button class=\"cat\" type=\"button\" data-topic=\"" + (k) + "\"><b>" + (esc(v.title)) + "</b><span>" + (esc(v.desc)) + "</span></button>")).join('')) + "</div>"), icon('send', 20), isParent ? ' ребёнка' : '', chatStrip(isAi)]),    mount(root) {
      const push = (q, a) => { chat.push({ me: true, text: q }, { me: false, text: a }); rerender(); window.scrollTo(0, document.body.scrollHeight); };
      const openChat = (id) => store.aiHistory(id).then((list) => {
        loadedChat = id;
        setChatId(id);
        setChat(list.filter((m) => m.content).map((m) => ({ me: m.role === 'user', text: m.content })));
        rerender();
      });

      if (isAi) {
        if (!chatsLoaded) {
          store.aiChats().then((list) => {
            setChats(list);
            if (list.length && !chatId) { openChat(list[0].id); return; }
            rerender();
          });
        } else if (chatId && !chat.length && loadedChat !== chatId) {
          openChat(chatId);
        }
      }

      root.querySelectorAll('[data-sub]').forEach((b) => b.addEventListener('click', () => push(b.dataset.sub, T[topic].subs[b.dataset.sub]())));
      root.querySelectorAll('[data-topic]').forEach((b) => b.addEventListener('click', () => {
        const k = b.dataset.topic;
        if (!k) { setTopic(null); rerender(); return; }
        setTopic(k);
        chat.push({ me: true, text: T[k].title }, { me: false, text: T[k].ask });
        rerender();
        window.scrollTo(0, document.body.scrollHeight);
      }));
      root.querySelectorAll('[data-chat]').forEach((b) => b.addEventListener('click', () => {
        const id = Number(b.dataset.chat);
        if (id === chatId) return;
        openChat(id);
      }));
      root.querySelectorAll('[data-new]').forEach((b) => b.addEventListener('click', () => {
        setChatId(null);
        setChat([]);
        loadedChat = null;
        rerender();
      }));
      root.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', () => {
        const id = Number(b.dataset.del);
        if (!window.confirm('Удалить чат?')) return;
        store.aiDeleteChat(id).then((ok) => {
          if (!ok) { toast('Не удалось удалить чат.'); return; }
          setChats(chats.filter((c) => c.id !== id));
          if (chatId === id) { setChatId(null); setChat([]); loadedChat = null; }
          rerender();
        });
      }));
      root.querySelectorAll('[data-confirm]').forEach((b) => b.addEventListener('click', () => {
        const message = chat[Number(b.dataset.confirm)];
        if (!message || !message.pending) return;
        b.disabled = true;
        store.aiAction(Object.assign({}, message.pending, { chat_id: chatId })).then((result) => {
          message.pending = null;
          chat.push({ me: false, text: result ? result.reply : 'Не удалось выполнить действие.' });
          if (result && /^task_/.test(result.action || '')) store.refreshData().then(() => rerender());
          rerender();
          window.scrollTo(0, document.body.scrollHeight);
        });
      }));
      root.querySelector('#ask').addEventListener('submit', (e) => {
        e.preventDefault();
        const q = root.querySelector('#q').value.trim();
        if (!q) return;
        setTopic(null);
        if (!isAi) { push(q, freeAnswer(q)); return; }
        const newChat = !chatId;
        chat.push({ me: true, text: q }, { me: false, text: 'Думаю…' });
        rerender(); window.scrollTo(0, document.body.scrollHeight);
        store.askAi(q, { chatId, newChat }).then((result) => {
          if (!result) {
            chat[chat.length - 1] = { me: false, text: 'Сервис ИИ недоступен' };
            chat.push({ me: false, text: freeAnswer(q) });
            rerender(); window.scrollTo(0, document.body.scrollHeight);
            return;
          }
          if (result.chat_id) {
            if (newChat) setChats([{ id: result.chat_id, title: result.title || q.slice(0, 60) }].concat(chats.filter((c) => c.id !== result.chat_id)));
            setChatId(result.chat_id);
            loadedChat = result.chat_id;
          }
          const pending = result.data && result.data.pending ? result.data.pending : null;
          chat[chat.length - 1] = { me: false, text: result.reply || '…', pending };
          if (result.action && /^task_/.test(result.action)) { store.refreshData().then(() => rerender()); window.scrollTo(0, document.body.scrollHeight); return; }
          rerender(); window.scrollTo(0, document.body.scrollHeight);
        });
      });
      root.querySelector('#reset').addEventListener('click', () => {
        setChat([]);
        setTopic(null);
        setChatId(null);
        loadedChat = null;
        rerender();
      });
    }
  };
}
export { aiView };
