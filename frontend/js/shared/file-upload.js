// Вложения формы: выбор, перетаскивание в форму и отмена добавления.
// Снятый чип удаляет и файл на сервере; если запрос не дошёл, сироту
// добьёт ежедневная чистка (app.routers.messages.cleanup_orphan_attachments).
import { esc, icon, toast } from '../ui.js';
import * as store from '../store.js';

export function createFileList({ input, drop, list }) {
  const files = [];
  const draw = () => {
    list.innerHTML = files.map((f, i) =>
      '<span class="pill file-chip' + (f.pending ? ' is-pending' : '') + '">'
      + icon('clip', 14) + ' ' + esc(f.name)
      + '<button class="file-x" type="button" data-file-remove="' + i + '" aria-label="Убрать файл ' + esc(f.name) + '">' + icon('close', 14) + '</button>'
      + '</span>').join('');
    list.querySelectorAll('[data-file-remove]').forEach((b) => b.addEventListener('click', () => {
      const [item] = files.splice(Number(b.dataset.fileRemove), 1);
      draw();
      // Токена ещё нет, пока файл грузится: удалим его после ответа.
      if (item && item.token) store.deleteAttachment(item.token);
    }));
  };
  const add = (picked) => {
    if (!picked.length) return;
    const items = picked.map((x) => ({ name: x.name, token: '', pending: true }));
    files.push(...items);
    draw();
    picked.forEach((x, i) => {
      const item = items[i];
      store.uploadAttachment(x).then((result) => {
        // Файл убрали, пока он грузился: чип не возвращаем, файл удаляем.
        if (!files.includes(item)) {
          if (result.token) store.deleteAttachment(result.token);
          return;
        }
        item.token = result.token || '';
        item.name = result.name || x.name;
        delete item.pending;
        draw();
      }).catch((error) => {
        const at = files.indexOf(item);
        if (at >= 0) files.splice(at, 1);
        draw();
        toast((error && error.message) || 'Не удалось загрузить файл');
      });
    });
  };
  input?.addEventListener('change', (e) => {
    const picked = [...e.target.files];
    e.target.value = '';
    add(picked);
  });
  if (drop) {
    const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
    drop.addEventListener('dragover', (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
      drop.classList.add('drop-over');
    });
    drop.addEventListener('dragleave', (e) => {
      if (!drop.contains(e.relatedTarget)) drop.classList.remove('drop-over');
    });
    drop.addEventListener('drop', (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      drop.classList.remove('drop-over');
      add([...(e.dataTransfer.files || [])]);
    });
  }
  draw();
  return { files, pending: () => files.some((f) => f.pending) };
}
