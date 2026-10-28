import * as store from '../store.js';

import { esc } from "../ui.js";

const periodLabel = () => esc(store.currentPeriod().name);

const periodEnd = () => store.currentPeriod().to;

const ui = { key: '8a-algebra', sel: null, lesson: null };

let chat = { id: null, msgs: [] };

let classTab = 'att';

store.onContextChange(() => { ui.key = null; ui.sel = null; ui.lesson = null; chat = { id: null, msgs: [] }; classTab = 'att'; });
export { periodLabel, periodEnd, ui, chat, classTab };

export const setChat = (value) => { chat = value; };
export const setClassTab = (value) => { classTab = value; };
