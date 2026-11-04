import * as store from '../store.js';

let chat = [];

let topic = null;

let chats = [];

let chatId = null;

store.onContextChange(() => { chat = []; topic = null; chats = []; chatId = null; });
export { chat, topic, chats, chatId };

export const setChat = (value) => { chat = value; };
export const setTopic = (value) => { topic = value; };
export const setChats = (value) => { chats = value; };
export const setChatId = (value) => { chatId = value; };
