import * as store from '../store.js';

let query = '';

store.onContextChange(() => { query = ''; });
export { query };

export const setQuery = (value) => { query = value; };
