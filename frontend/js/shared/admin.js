import { fragment } from '../templates.js';
import * as store from '../store.js';
import { esc, toast } from "../ui.js";
import { confirmSheet } from "../common.js";
import { copyCode } from '../clipboard.js';

const shortName = (n) => { const [f, i, o] = n.split(' '); return f + (i ? ' ' + i[0] + '.' : '') + (o ? ' ' + o[0] + '.' : ''); };

const subjName = (k) => (store.SUBJECTS[k] ? store.SUBJECTS[k].name : k);

const accessHtml = (key) => {
  const invite = store.invitation(key);
  return !invite ? fragment("shared-admin", "fragment-1") : invite.used ? fragment("shared-admin", "fragment-2") : fragment("shared-admin", "fragment-3", [copyCode(invite.invite_code, 'приглашение'), esc(key)]);
};

const bindInvites = (root, rerender) => root.querySelectorAll('[data-renew]').forEach((b) => b.addEventListener('click', () => confirmSheet('Заменить приглашение?', 'Прежний код перестанет действовать.', 'Заменить', () => { if (!store.invitation(b.dataset.renew, true)) return toast(store.SAVE_ERROR); rerender(); })));
export { shortName, subjName, accessHtml, bindInvites };
