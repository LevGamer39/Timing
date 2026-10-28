const templates = new Map();

export function registerFragments(page, source) {
  const fragments = new Map();
  const pattern = /<!-- fragment:([\w-]+) -->\r?\n([\s\S]*?)\r?\n<!-- \/fragment:\1 -->/g;
  for (const match of source.matchAll(pattern)) fragments.set(match[1], match[2]);
  templates.set(page, fragments);
}

export async function loadTemplates() {
  if (globalThis.__VECTOR_TEMPLATES__) {
    for (const [page, source] of Object.entries(globalThis.__VECTOR_TEMPLATES__)) registerFragments(page, source);
    return;
  }
  const base = new URL('../html/', import.meta.url);
  const response = await fetch(new URL('manifest.json', base));
  if (!response.ok) throw new Error('Не удалось загрузить список экранов');
  const pages = await response.json();
  await Promise.all(pages.map(async (page) => {
    const result = await fetch(new URL(page + '.html', base));
    if (!result.ok) throw new Error('Не удалось загрузить экран ' + page);
    registerFragments(page, await result.text());
  }));
}

export function fragment(page, name, values = []) {
  const source = templates.get(page)?.get(name);
  if (source === undefined) throw new Error('Шаблон не загружен: ' + page + '/' + name);
  return source.replace(/\{\{(\d+)\}\}/g, (_, index) => values[Number(index)] ?? '');
}
