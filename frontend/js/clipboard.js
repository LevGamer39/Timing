import { esc, icon, toast, sheet } from './ui.js';

export const copyCode = (value, label = 'код') => `<span class="code-copy-wrap"><button class="code-copy" type="button" data-copy="${esc(value)}" aria-label="Скопировать ${esc(label)}"><code class="code-blur" data-code-blur title="Нажми, чтобы показать">${esc(value)}</code><span class="copy-caption" data-copy-label>Скопировать</span>${icon('copy', 18)}</button><span class="hint code-hint">код скрыт — нажми, чтобы показать</span></span>`;

export async function copyText(value, environment = globalThis) {
  try {
    await environment.navigator.clipboard.writeText(value);
    return true;
  } catch {
    const doc = environment.document, active = doc.activeElement;
    const input = doc.createElement('textarea');
    input.value = value; input.readOnly = true;
    input.style.cssText = 'position:fixed;inset:0 auto auto 0;opacity:0;pointer-events:none';
    doc.body.append(input); input.select();
    try { return !!doc.execCommand('copy'); }
    catch { return false; }
    finally { input.remove(); active?.focus({ preventScroll: true }); }
  }
}

export function bindCopy(root) {
  root.addEventListener('click', async (event) => {
    const blur = event.target.closest('[data-code-blur]');
    if (blur) {
      blur.classList.toggle('revealed');
      const hint = blur.closest('.code-copy-wrap')?.querySelector('.code-hint');
      if (hint) hint.textContent = blur.classList.contains('revealed') ? 'код виден — копируй' : 'код скрыт — нажми, чтобы показать';
      return;
    }
    const button = event.target.closest('[data-copy]');
    if (!button || button.dataset.copying === 'true') return;
    button.dataset.copying = 'true';
    const value = button.dataset.copy;
    try {
      if (await copyText(value)) {
        const label = button.querySelector('[data-copy-label]');
        const old = label?.textContent;
        button.dataset.copied = 'true';
        if (label) label.textContent = 'Скопировано';
        toast('Скопировано');
        setTimeout(() => { delete button.dataset.copied; if (label) label.textContent = old; }, 1800);
      } else {
        sheet(`<h2 class="h2">Скопируйте код</h2><p class="hint">Браузер не разрешил копирование. Выделите код и скопируйте вручную.</p><label class="sr-only" for="copy-manually">Код</label><input class="input" id="copy-manually" readonly value="${esc(value)}"><button class="btn ghost block" type="button" data-close style="margin-top: 16px">Закрыть</button>`, (el) => el.querySelector('input').select());
      }
    } finally { delete button.dataset.copying; }
  });
}
