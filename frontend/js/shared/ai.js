import * as store from '../store.js';

let chat = [];

let topic = null;

let chats = [];

let chatId = null;

let chatsLoaded = false;

store.onContextChange(() => { chat = []; topic = null; chats = []; chatId = null; chatsLoaded = false; });
export { chat, topic, chats, chatId, chatsLoaded };

export const setChat = (value) => { chat = value; };
export const setTopic = (value) => { topic = value; };
// Пустой ответ тоже считается загруженным: иначе mount() запрашивает список
// и перерисовывает экран без остановки.
export const setChats = (value) => { chats = value; chatsLoaded = true; };
export const setChatId = (value) => { chatId = value; };
