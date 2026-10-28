// В хранилище баллы остаются в диапазоне 0–100. Ввод и показ — в шкале школы.
let scale = 100;
let bounds = [40, 60, 80];
export function configureGrading(max, limits) { scale = max; bounds = limits.slice(); }
export const gradeScale = () => scale;
export const gradeMin = () => scale === 5 ? 1 : 0;
export const score = (v, round = true) => {
  if (typeof v !== 'number') return v;
  const n = v * scale / 100;
  return round ? Math.max(gradeMin(), Math.min(scale, Math.round(n))) : n;
};
export const storedScore = (v) => v * 100 / scale;
export const normalizedScore = (v) => typeof v === 'number' ? storedScore(score(v)) : v;
export const gradeMark = (v) => { const n = score(v, false); return n >= bounds[2] ? 5 : n >= bounds[1] ? 4 : n >= bounds[0] ? 3 : 2; };
export const normalizedBounds = () => bounds.map(storedScore);
export const gradeRanges = () => [gradeMin(), ...bounds].map((from, i) => ({ mark: i + 2, from, to: i === 3 ? scale : bounds[i] - 1 }));
