
    const API = window.location.origin;
    const TOKEN = localStorage.getItem('token');
    const HEADERS = { 'Authorization': `Bearer ${TOKEN}`, 'Content-Type': 'application/json' };
    const ROLE_ROUTES = { student: '/student/dashboard', teacher: '/teacher/dashboard', parent: '/parent/dashboard', admin: '/admin/dashboard' };

    if (!TOKEN) window.location.href = '/login';

    let myChart = null;

    // ─── УТИЛИТЫ ───────────────────────────────────────────────────────────────

    // Убираем markdown-разметку: **жирный**, *курсив*, ## заголовки, маркеры
    function stripMarkdown(text) {
        return text
            .replace(/#{1,6}\s*/g, '')          // headers
            .replace(/\*\*(.+?)\*\*/g, '$1')    // bold
            .replace(/\*(.+?)\*/g, '$1')         // italic
            .replace(/`(.+?)`/g, '$1')           // inline code
            .replace(/^[\-\*]\s+/gm, '• ')       // bullet points → nice dot
            .replace(/\n{3,}/g, '\n\n')          // excessive newlines
            .trim();
    }

    function formatNotifTime(isoStr) {
        const date = new Date(isoStr);
        const now = new Date();
        const diffMs = now - date;
        const diffDays = Math.floor(diffMs / 86400000);
        if (diffDays === 0) return date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
        if (diffDays === 1) return 'ВЧЕРА, ' + date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
        return date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' });
    }

    function set(id, v) {
        const el = document.getElementById(id);
        if (el) el.textContent = v;
    }

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    // ─── ЗАПУСК ─────────────────────────────────────────────────────────────────

    async function init() {
        try {
            const [userRes, taskRes] = await Promise.all([
                fetch(`${API}/api/users/me`, { headers: HEADERS }),
                fetch(`${API}/api/tasks`, { headers: HEADERS }),
            ]);

            if (!userRes.ok) { window.location.href = '/login'; return; }

            const user = await userRes.json();
            if (user.role !== 'student') { window.location.replace(ROLE_ROUTES[user.role] || '/login'); return; }
            const tasks = await taskRes.json();

            // Инициалы для аватара
            const initials = (user.first_name?.[0] || '') + (user.last_name?.[0] || '');
            const avatarEl = document.querySelector('.avatar');
            if (avatarEl) {
                avatarEl.textContent = initials.toUpperCase();
                avatarEl.style.backgroundImage = user.avatar_url ? `url("${user.avatar_url}")` : '';
                avatarEl.style.color = user.avatar_url ? 'transparent' : '#fff';
            }
            set('userName', `${user.first_name} ${user.last_name}!`);

            // Сравнение по локальной дате — иначе UTC и локальное время разойдутся
            const todayStr = toLocalDateStr(new Date());

            const todayTasks = tasks.filter(t => t.deadline && toLocalDateStr(new Date(t.deadline)) === todayStr);
            const activeTasks = tasks.filter(t => t.status !== 'done');

            const done = todayTasks.filter(t => t.status === 'done').length;
            const total = todayTasks.length;
            const pct = total > 0 ? Math.round((done / total) * 100) : 0;

            updateProgressUI(pct, done, total);
            renderTasks(activeTasks);
            updateChart(tasks);           // pass tasks directly, no extra fetch
            updateAISummary(activeTasks, user.first_name);
            loadNotifications();

        } catch (err) {
            console.error('Ошибка инициализации:', err);
        }
    }

    // ─── КОЛЬЦО ПРОГРЕССА ───────────────────────────────────────────────────────

    function updateProgressUI(p, d, t) {
        const ring = document.getElementById('progressRing');
        const dot = document.getElementById('progressDot');
        const wrap = document.getElementById('progressWrap');

        if (ring) {
            const r = 82;
            const circ = 2 * Math.PI * r;
            ring.style.strokeDasharray = circ;
            const offset = circ - (p / 100) * circ;
            ring.style.strokeDashoffset = offset;

            // Цвет по проценту
            function getColor(percent) {
                if (percent <= 25) return '#DB0505';       // red
                if (percent <= 50) return '#E2A804';       // orange
                if (percent <= 75) return '#8BC34A';       // light green
                return '#1a7a00';                          // dark green
            }

            const color = getColor(p);
            ring.style.stroke = color;
            ring.style.filter = `drop-shadow(0 0 8px ${color})`;
        }

        // Позиция точки прогресса с учётом размера
        if (dot && wrap) {
            function positionDot() {
                const size = wrap.offsetWidth;
                if (!size) {
                    requestAnimationFrame(positionDot);
                    return;
                }

                const cx = size / 2, cy = size / 2;
                const radius = (82 / 200) * size;
                const angleDeg = -90 + (p / 100) * 360;
                const angleRad = (angleDeg * Math.PI) / 180;
                const x = cx + radius * Math.cos(angleRad);
                const y = cy + radius * Math.sin(angleRad);
                const dotSize = Math.max(10, size * 0.075);

                dot.style.width = dotSize + 'px';
                dot.style.height = dotSize + 'px';
                dot.style.left = (x - dotSize / 2) + 'px';
                dot.style.top = (y - dotSize / 2) + 'px';
                dot.style.background = '#fff';
                dot.style.boxShadow = `0 0 10px rgba(255,255,255,0.9)`;
            }

            positionDot();
            // Пересчёт при изменении размера окна
            if (!window._progressResizeListener) {
                window._progressResizeListener = true;
                window.addEventListener('resize', positionDot);
            }
            setTimeout(positionDot, 200);
        }

        set('progressText', p + '%');
        set('progressLabel', `Прогресс сегодня: ${p}%`);
        set('progressSublabel', `Выполнено ${d} из ${t}`);
        set('dynamicPercent', p + '%');
        const dynamicLabel = document.getElementById('dynamicLabel');
        if (dynamicLabel) dynamicLabel.innerHTML = 'Продуктивность<br>за сегодня';
        set('dynamicSublabel', `${d} из ${t} выполнено`);
    }

    // ─── СПИСОК ЗАДАЧ ──────────────────────────────────────────────────────────

    function renderTasks(activeTasks) {
        const container = document.getElementById('taskList');
        if (!container) return;
        const header = container.querySelector('.card-tasks-header');

        const COLOR_MAP = { critical: 'red', high: 'red', medium: 'yellow', low: 'green' };
        const LABEL_MAP = { critical: 'Критично', high: 'Критично', medium: 'Важно', low: 'Обычное' };

        let html = '';
        activeTasks.slice(0, 3).forEach(t => {
            const color = COLOR_MAP[t.priority] || 'green';
            const label = LABEL_MAP[t.priority] || 'Обычное';
            const deadlineText = t.deadline
                ? new Date(t.deadline).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
                : (t.subject || 'Общее');
            html += `
                <div class="task-item" onclick="completeTask(${t.id})" style="cursor:pointer" title="${escapeHtml(t.title)}">
                    <div class="task-stripe ${color}"></div>
                    <div class="task-body">
                        <span class="task-priority ${color}">${label}</span>
                        <div class="task-name">${escapeHtml(t.title)}</div>
                        <div class="task-time">
                            <svg viewBox="0 0 24 24" fill="none" style="width:13px;flex-shrink:0">
                                <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/>
                                <path d="M12 7v5l3 3" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                            </svg>
                            ${escapeHtml(deadlineText)}
                        </div>
                    </div>
                </div>`;
        });

        container.innerHTML = (header ? header.outerHTML : '') +
            (html || '<div class="task-item"><div class="task-body" style="padding:12px 14px;opacity:.6">Активных задач нет 🎉</div></div>');
    }

    async function completeTask(id) {
        if (!confirm('Отметить задачу как выполненную?')) return;
        try {
            const res = await fetch(`${API}/api/tasks/${id}/complete`, { method: 'POST', headers: HEADERS });
            if (res.ok) init();
        } catch (e) { console.error(e); }
    }

    // ─── УТИЛИТЫ ───────────────────────────────────────────────────────────────

    function toLocalDateStr(d) {
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    }

    // ─── ГРАФИК (процент закрытых задач по дням недели) ───────────────────

    function updateChart(tasks) {
        const canvas = document.getElementById('perfCanvas');
        if (!canvas) return;

        const today = new Date();
        const dow = today.getDay(); // 0=Sun
        const diffToMon = (dow === 0) ? -6 : 1 - dow;
        const monday = new Date(today);
        monday.setDate(today.getDate() + diffToMon);
        monday.setHours(0, 0, 0, 0);

        const DAY_LABELS = ['ПН', 'ВТ', 'СР', 'ЧТ', 'ПТ', 'СБ', 'ВС'];
        const todayIdx = (dow === 0) ? 6 : dow - 1; // 0=Mon…6=Sun

        // Для каждого дня считаем задачи, чей дедлайн попадает на него
        const rawValues = DAY_LABELS.map((_, i) => {
            const day = new Date(monday);
            day.setDate(monday.getDate() + i);
            const dStr = toLocalDateStr(day);
            const dayTasks = tasks.filter(t => t.deadline && toLocalDateStr(new Date(t.deadline)) === dStr);
            if (dayTasks.length === 0) return null; // no tasks scheduled → no point
            const dDone = dayTasks.filter(t => t.status === 'done').length;
            return Math.round((dDone / dayTasks.length) * 100);
        });

        // Chart.js нужны числа; вместо null подставляем 0, но стилизуем иначе
        const renderValues = rawValues.map(v => v === null ? 0 : v);

        // Стиль: сегодняшняя точка фиолетовая, будущие и пустые приглушены
        const pointRadii = renderValues.map((_, i) => i === todayIdx ? 7 : (rawValues[i] !== null ? 4 : 2));
        const pointColors = renderValues.map((_, i) => i === todayIdx ? '#8B79FF' : (rawValues[i] !== null ? '#ffffff' : 'rgba(255,255,255,0.2)'));

        if (myChart) myChart.destroy();
        myChart = new Chart(canvas.getContext('2d'), {
            type: 'line',
            data: {
                labels: DAY_LABELS,
                datasets: [{
                    data: renderValues,
                    borderColor: 'rgba(255,255,255,0.85)',
                    backgroundColor: 'rgba(255,255,255,0.07)',
                    borderWidth: 2.5,
                    tension: 0.35,
                    fill: true,
                    pointRadius: pointRadii,
                    pointBackgroundColor: pointColors,
                    pointBorderWidth: 0,
                    pointHoverRadius: 8,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
				resizeDelay: 150,
				devicePixelRatio: 2,
				onResize: function(chart, size) {
					chart.canvas.style.height = '180px';
				},
                animation: { duration: 700, easing: 'easeOutQuart' },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            title: items => DAY_LABELS[items[0].dataIndex],
                            label: ctx => rawValues[ctx.dataIndex] !== null
                                ? ` ${rawValues[ctx.dataIndex]}% выполнено`
                                : ' нет задач на этот день'
                        }
                    }
                },
                scales: {
                    y: { min: 0, max: 100, display: false },
                    x: { display: false }
                },
				layout: {
                padding: {
                    top: 10,
                    bottom: 10,
                    left: 5,
                    right: 5
                }
            }
				
            }
        });
    }

    // ─── ИИ-СВОДКА ──────────────────────────────────────────────────────────

    // Основная функция для работы с ИИ
    async function updateAISummary(tasks, name) {
        const el = document.getElementById('aiSummary');
        if (!el) return;
		if (!tasks || tasks.length === 0) {
			el.innerHTML = `Привет, <b>${name}</b>!<br>На сегодня задач нет. Ты всё выполнил, отличная работа! Отдыхай или добавь новые цели.`;
			return;
		}
		el.innerHTML = "Составляю план...";
        try {
            // Собираем не только заголовки, но и предметы, чтобы ИИ понимал контекст
            const taskDetails = tasks.map(t => `- ${t.title} (Предмет: ${t.subject || 'Общее'})`).join('\n');
            const res = await fetch(`${API}/api/ai/chat`, {
                method: 'POST',
                headers: HEADERS,
                body: JSON.stringify({
                    message: `
                КОНТЕКСТ: Ты — ИИ-ментор "Вектор". Ученик: ${name}. Задачи: ${taskDetails}.
                
                ИНСТРУКЦИЯ:
                1. Приветствуй коротко: "Привет, ${name}!"
                2. Разбей задачи на подпункты.
                3. Дай совет по приоритетам.
				4. Если список задач пустой скажи молодец что всё выполнил.
                
                ЖЕСТКИЕ ПРАВИЛА:
                - ЯЗЫК: Отвечай ТОЛЬКО на русском языке. Использование китайских иероглифов или английского ЗАПРЕЩЕНО.
                - ФОРМАТ: Пиши плотно. Запрещено более одного переноса строки (\n) подряд.
                - ОФОРМЛЕНИЕ: Используй только <b> и <br>.
                - НИКАКОЙ ВОДЫ.`
                })
            });

            const data = await res.json();
			
            const aiText = data.response || data.reply || data.text || "План готов, приступай!";
            el.innerHTML = aiText.replace(/\n/g, '<br>');

        } catch (e) {
            console.error("AI Error:", e);
            el.textContent = "ИИ временно недоступен.";
        }
    }

    // ─── УВЕДОМЛЕНИЯ ───────────────────────────────────────────────────────

    async function loadNotifications() {
        try {
            const res = await fetch(`${API}/api/notifications`, { headers: HEADERS });
            if (!res.ok) return;
            const notifications = await res.json();

            const unread = notifications.filter(n => !n.is_read);
            const badge = document.getElementById('notifCounter');
            if (badge) {
                if (unread.length > 0) {
                    badge.textContent = unread.length > 99 ? '99+' : unread.length;
                    badge.style.display = 'flex';
                } else {
                    badge.style.display = 'none';
                }
            }

            renderNotificationList(notifications);
        } catch (_) { }
    }

    function renderNotificationList(notifications) {
        const list = document.getElementById('notifList');
        if (!list) return;

        if (!notifications.length) {
            list.innerHTML = '<div style="color:rgba(255,255,255,.4);text-align:center;margin-top:40px;font-size:13px;">Уведомлений нет</div>';
            return;
        }

        list.innerHTML = notifications.map(n => {
            const timeStr = formatNotifTime(n.created_at);
            // Обрезаем заголовок до 32 символов
            const title = n.title.length > 32 ? n.title.slice(0, 32) + '…' : n.title;
            const unreadDot = !n.is_read ? '<span class="notif-unread-dot"></span>' : '';
            return `
                <div class="notif-item${n.is_read ? '' : ' notif-item--unread'}" onclick="markNotifRead(${n.id}, this)">
                    ${unreadDot}
                    <div class="notif-author">${n.channel === 'email' ? '📧' : '🔔'} Система</div>
                    <div class="notif-time">${timeStr}</div>
                    <div class="notif-text" title="${n.title}">${title}</div>
                </div>`;
        }).join('');
    }

    async function markNotifRead(id, el) {
        try {
            await fetch(`${API}/api/notifications/${id}/read`, { method: 'POST', headers: HEADERS });
            el.classList.remove('notif-item--unread');
            const dot = el.querySelector('.notif-unread-dot');
            if (dot) dot.remove();
            // Обновляем счётчик бейджа
            loadNotifications();
        } catch (_) { }
    }

    function toggleNotifications(event) {
        if (event) event.stopPropagation();
        const modal = document.getElementById('notifModal');
        if (!modal) return;
        const isOpen = modal.style.display === 'flex';
        modal.style.display = isOpen ? 'none' : 'flex';
        if (!isOpen) loadNotifications();
    }

    // ─── ИНИЦИАЛИЗАЦИЯ ─────────────────────────────────────────────────────────────────

    document.addEventListener('DOMContentLoaded', () => {
        init();

        // Закрываем окно уведомлений по клику вне него
        document.addEventListener('click', e => {
            const modal = document.getElementById('notifModal');
            const wrap = document.querySelector('.notif-wrap');
            if (modal && wrap && !wrap.contains(e.target)) {
                modal.style.display = 'none';
            }
        });

        const modal = document.getElementById('notifModal');
        if (modal) modal.addEventListener('click', e => e.stopPropagation());
    });
	
	const currentPath = window.location.pathname;

    document.querySelectorAll('.nav-item').forEach(link => {
        const href = link.getAttribute('href');

        if (href === currentPath) {
            link.classList.add('active');
        }
    });
