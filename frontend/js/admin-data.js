// Демо-режим удалён. Фронтенд работает только с сервером через JWT.
// Эти структуры — заглушки для совместимости с server.js.

import { SCHEDULE_8A, SCHEDULE_4B } from './data.js';

const TR = { а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ё: 'e', ж: 'zh', з: 'z', и: 'i', й: 'y', к: 'k', л: 'l', м: 'm', н: 'n', о: 'o', п: 'p', р: 'r', с: 's', т: 't', у: 'u', ф: 'f', х: 'kh', ц: 'ts', ч: 'ch', ш: 'sh', щ: 'shch', ъ: '', ы: 'y', ь: '', э: 'e', ю: 'yu', я: 'ya' };
export const loginOf = (name) => name.toLowerCase().split(' ').slice(0, 2).map((w) => [...w].map((c) => TR[c] ?? c).join('')).join('.');

export const CLASSES = {};
export const TEACHERS = [];
export const SCHEDULES = {};
export const SCHOOL_SETTINGS = {
  scale: 100,
  periodType: 'quarters',
  days: 7,
  periods: { quarters: [], trimesters: [], halves: [] }
};
