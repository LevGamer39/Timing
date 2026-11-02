import * as store from '../store.js';
import { iso } from "../ui.js";

let period = null;

const periods = () => [...store.studyPeriods().filter((p) => p.from <= iso(store.NOW)), { key: 'year', name: 'Учебный год', from: null, to: null }];

let goalChat = { subj: null, msgs: [] };

store.onContextChange(() => { period = null; goalChat = { subj: null, msgs: [] }; });
export { period, periods, goalChat };

export const setPeriod = (value) => { period = value; };
export const setGoalChat = (value) => { goalChat = value; };
