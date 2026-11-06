// Встроенный просмотр файлов: полноэкранный viewer.
// Картинки — панорама + зум (колесо, кнопки, pinch, drag, двойной тап).
// PDF — на всё окно (только документ, без редактора). Остальное — скачивание.

import { esc, toast } from '../ui.js';
import * as api from '../api.js';

const IMAGE_EXT = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'avif', 'heic', 'heif'];
const PDF_EXT = ['pdf'];

export function fileKind(name) {
  const ext = String(name || '').split('.').pop().toLowerCase();
  if (IMAGE_EXT.includes(ext)) return 'image';
  if (PDF_EXT.includes(ext)) return 'pdf';
  return 'download';
}

export function fileChip(file) {
  const kind = fileKind(file.name);
  return `<button class="pill file-pill" type="button" data-preview-file data-token="${esc(file.token || '')}" data-name="${esc(file.name)}" title="${kind === 'download' ? 'Скачать' : 'Просмотреть'}">${kind === 'download' ? '↓' : '⤢'} ${esc(file.name)}</button>`;
}

function downloadUrl(url, name) {
  const a = document.createElement('a');
  a.href = url;
  a.download = name || 'файл';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export async function openFilePreview(token, name) {
  if (!token) return toast('Файл был приложен без загрузки — скачать его нельзя.');
  const kind = fileKind(name);
  if (kind === 'download') {
    try {
      const blob = await api.downloadAttachment(token);
      downloadUrl(URL.createObjectURL(blob), name);
    } catch {
      toast('Не удалось скачать файл');
    }
    return;
  }
  try {
    // PDF: рендерим страницы на сервере в PNG — полноэкранная картинка с зумом/панорамой,
    // без встроенного просмотрщика Chrome (тулбар, страница в углу).
    if (kind === 'pdf') {
      const pngBlob = await fetchRenderedPage(token, 0);
      const url = URL.createObjectURL(pngBlob);
      showImageFullscreen(url, name + '.png');
      return;
    }
    const blob = await api.downloadAttachment(token);
    const url = URL.createObjectURL(blob);
    const mime = blob.type || '';
    if (mime.startsWith('image/')) {
      showImageFullscreen(url, name);
      return;
    }
    downloadUrl(url, name);
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  } catch {
    toast('Не удалось открыть файл');
  }
}

async function fetchRenderedPage(token, page) {
  const headers = {};
  if (api.token && api.token()) headers.Authorization = 'Bearer ' + api.token();
  const base = (typeof location !== 'undefined' && ['127.0.0.1', 'localhost'].includes(location.hostname) && location.port && location.port !== '8000') ? 'http://127.0.0.1:8000' : '';
  const response = await fetch(base + '/api/messages/attachments/' + encodeURIComponent(token) + '/render?page=' + page, { headers });
  if (!response.ok) throw new Error('render failed');
  return response.blob();
}

function showImageFullscreen(url, name) {
  const root = document.createElement('div');
  root.className = 'file-viewer';
  root.innerHTML = `
    <div class="file-viewer-top">
      <span class="file-viewer-name">${esc(name)}</span>
      <span class="row" style="gap:6px">
        <button class="btn ghost sm" type="button" data-z="-">−</button>
        <span class="muted small" data-zl>100%</span>
        <button class="btn ghost sm" type="button" data-z="+">+</button>
        <button class="btn ghost sm" type="button" data-zreset>Сброс</button>
        <button class="btn sm" type="button" data-dl>Скачать</button>
        <button class="btn ghost sm" type="button" data-close>Закрыть</button>
      </span>
    </div>
    <div class="file-stage"><img data-img src="${url}" alt="${esc(name)}"></div>
    <p class="file-viewer-hint">Колесо / кнопки — зум · перетаскивание — движение · щипок — зум пальцами · двойной тап — 100% / 200%</p>`;

  document.body.appendChild(root);
  document.body.style.overflow = 'hidden';

  const stage = root.querySelector('.file-stage');
  const img = root.querySelector('[data-img]');
  const label = root.querySelector('[data-zl]');
  const pointers = new Map();
  let scale = 1;
  let tx = 0, ty = 0;
  let startTx = 0, startTy = 0;
  let pinchDist = 0;
  let lastTap = 0;

  const apply = () => {
    img.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
    label.textContent = Math.round(scale * 100) + '%';
  };
  const clamp = () => {
    const max = (scale - 1) * (stage.clientWidth / 2);
    const maxY = (scale - 1) * (stage.clientHeight / 2);
    tx = Math.max(-Math.abs(max) - 40, Math.min(Math.abs(max) + 40, tx));
    ty = Math.max(-Math.abs(maxY) - 40, Math.min(Math.abs(maxY) + 40, ty));
  };
  const zoomAt = (cx, cy, factor) => {
    const ns = Math.max(0.5, Math.min(4, scale * factor));
    if (ns === scale) return;
    scale = ns;
    clamp();
    apply();
  };

  root.querySelectorAll('[data-z]').forEach((b) => b.addEventListener('click', () => zoomAt(0, 0, b.dataset.z === '+' ? 1.25 : 0.8)));
  root.querySelector('[data-zreset]').addEventListener('click', () => { scale = 1; tx = ty = 0; apply(); });
  root.querySelector('[data-dl]').addEventListener('click', () => downloadUrl(url, name));

  const close = () => { root.remove(); document.body.style.overflow = ''; try { URL.revokeObjectURL(url); } catch {} };
  root.querySelector('[data-close]').addEventListener('click', close);
  document.addEventListener('keydown', function onKey(e) {
    if (e.key === 'Escape') { close(); document.removeEventListener('keydown', onKey); }
  });

  stage.addEventListener('pointerdown', (e) => {
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    stage.setPointerCapture(e.pointerId);
    stage.style.touchAction = 'none';
    startTx = tx;
    startTy = ty;
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      pinchDist = Math.hypot(a.x - b.x, a.y - b.y);
    }
    // двойной тап/клик
    const now = Date.now();
    if (now - lastTap < 350 && pointers.size === 1) {
      scale = scale > 1.05 ? 1 : 2;
      tx = ty = 0;
      apply();
    }
    lastTap = now;
  });
  stage.addEventListener('pointermove', (e) => {
    if (!pointers.has(e.pointerId)) return;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.size === 1) {
      const p = pointers.values().next().value;
      // дельта от старта указателя упрощённо: считаем от последнего движения указателя
      tx += e.movementX || 0;
      ty += e.movementY || 0;
    } else if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      if (pinchDist > 0) zoomAt(0, 0, d / pinchDist);
      pinchDist = d;
    }
    clamp();
    apply();
  });
  const endPointer = (e) => { pointers.delete(e.pointerId); pinchDist = 0; };
  stage.addEventListener('pointerup', endPointer);
  stage.addEventListener('pointercancel', endPointer);

  stage.addEventListener('wheel', (e) => {
    e.preventDefault();
    zoomAt(0, 0, e.deltaY < 0 ? 1.12 : 0.89);
  }, { passive: false });
}

export function bindFilePreviews(root) {
  root?.querySelectorAll('[data-preview-file]').forEach((b) => {
    b.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      openFilePreview(b.dataset.token || '', b.dataset.name || 'файл');
    });
  });
}
