import * as store from '../store.js';

let chat = [];

let topic = null;

store.onContextChange(() => { chat = []; topic = null; });
export { chat, topic };

export const setChat = (value) => { chat = value; };
export const setTopic = (value) => { topic = value; };
