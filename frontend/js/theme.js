export const ACCENTS = [
  { id: 'orange', name: 'Угли', light: '#B9521C', dark: '#E0743C', text: '#FFB38A' },
  { id: 'blue', name: 'Океан', light: '#235DB3', dark: '#79AEFF', text: '#A8CAFF' },
  { id: 'green', name: 'Лес', light: '#226B4B', dark: '#6FD5A6', text: '#9AEAC4' },
  { id: 'purple', name: 'Ирис', light: '#7440A6', dark: '#C59AEE', text: '#DCBEFF' },
  { id: 'rose', name: 'Ягода', light: '#A82F60', dark: '#F195B8', text: '#FFBDD6' }
];

export function applyAccent(id, theme, doc = document) {
  const palette = ACCENTS.find((p) => p.id === id) || ACCENTS[0];
  const resolved = theme === 'dark' ? 'dark' : 'light';
  const root = doc.documentElement;
  for (const name of ['accent', 'accent-text', 'accent-soft', 'on-accent', 'nav-on', 'nav-on-text', 'bg-grad', 'bg-base', 'bg-glow']) root.style.removeProperty('--' + name);
  root.dataset.theme = resolved;
  root.dataset.accent = palette.id;
  doc.querySelector('meta[name="theme-color"]')?.setAttribute('content', resolved === 'dark' ? '#070708' : '#F6F4F1');
}
