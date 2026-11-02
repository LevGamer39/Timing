import { SCHEDULE_8A, SCHEDULE_4B } from './data.js';
import { N8A, N8B, N7V } from './teacher-data.js';

const N4B = ['Белова Соня', 'Власов Тимофей', 'Галкина Ульяна', 'Денисов Марк', 'Журова Ева', 'Карпов Савелий', 'Лебедева Мира', 'Тихонов Матвей'];

const TR = { а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ё: 'e', ж: 'zh', з: 'z', и: 'i', й: 'y', к: 'k', л: 'l', м: 'm', н: 'n', о: 'o', п: 'p', р: 'r', с: 's', т: 't', у: 'u', ф: 'f', х: 'kh', ц: 'ts', ч: 'ch', ш: 'sh', щ: 'shch', ъ: '', ы: 'y', ь: '', э: 'e', ю: 'yu', я: 'ya' };
export const loginOf = (name) => name.toLowerCase().split(' ').slice(0, 2).map((w) => [...w].map((c) => TR[c] ?? c).join('')).join('.');

const student = (name, i, parent) => ({
  name, login: loginOf(name),
  status: i % 5 === 2 ? 'wait' : 'ok',
  // parent: '' — не добавлен, 'выдан' — добавлен, иначе ФИО.
  parent: parent || (i % 4 === 3 ? '' : 'выдан')
});

export const CLASSES = {
  '8a': { key: '8a', name: '8А', teacher: 't-ivanova', students: N8A.map((n, i) => student(n, i, n === 'Белов Артём' ? 'Белова Анна Викторовна' : '')) },
  '8b': { key: '8b', name: '8Б', teacher: 't-sokolova', students: N8B.map((n, i) => student(n, i)) },
  '7v': { key: '7v', name: '7В', teacher: 't-morozov', students: N7V.map((n, i) => student(n, i)) },
  '4b': { key: '4b', name: '4Б', teacher: 't-zaitseva', students: N4B.map((n, i) => student(n, i, n === 'Белова Соня' ? 'Белова Анна Викторовна' : '')) }
};
CLASSES['4b'].students[7].status = 'wait';

export const TEACHERS = [
  { id: 't-ivanova', name: 'Иванова Мария Петровна', load: [['algebra', ['8a', '8b', '7v']], ['geometry', ['8a']]] },
  { id: 't-smirnova', name: 'Смирнова Ольга Викторовна', load: [['russian', ['8a']], ['literature', ['8a']]] },
  { id: 't-lebedeva', name: 'Лебедева Анна Сергеевна', load: [['english', ['8a']], ['english4', ['4b']]] },
  { id: 't-pavlov', name: 'Павлов Игорь Сергеевич', load: [['physics', ['8a', '8b']]] },
  { id: 't-sokolova', name: 'Соколова Вера Андреевна', load: [['chemistry', ['8a', '8b']]] },
  { id: 't-orlova', name: 'Орлова Наталья Ивановна', load: [['biology', ['8a', '8b', '7v']]] },
  { id: 't-morozov', name: 'Морозов Денис Олегович', load: [['history', ['8a', '7v']]] },
  { id: 't-kim', name: 'Ким Артур Викторович', load: [['informatics', ['8a', '8b']]] },
  { id: 't-volkov', name: 'Волков Сергей Петрович', load: [['pe', ['8a', '8b', '7v']], ['pe4', ['4b']]] },
  { id: 't-zaitseva', name: 'Зайцева Ирина Юрьевна', load: [['math', ['4b']], ['russian4', ['4b']], ['reading', ['4b']], ['world', ['4b']]] },
  { id: 't-belkina', name: 'Белкина Ольга Игоревна', load: [['art', ['4b']]] },
  { id: 't-gromova', name: 'Громова Лилия Петровна', load: [['music', ['4b']]] }
];

// Учителя общие с 8А и 4Б: номера уроков подобраны так, чтобы ни у кого не было двух классов одновременно.
const SCHEDULE_8B = [
  ['physics', 'algebra', 'chemistry', 'biology', 'pe'],
  ['informatics', 'algebra', 'chemistry', 'biology', 'physics'],
  ['algebra', 'chemistry', 'informatics', 'pe'],
  ['biology', 'physics', 'algebra', 'informatics', 'pe'],
  ['chemistry', 'algebra', 'physics', 'biology'],
  []
];
const SCHEDULE_7V = [
  ['pe', 'biology', 'algebra', 'history'],
  ['biology', 'history', 'algebra'],
  ['pe', 'history', 'algebra', 'biology'],
  ['history', 'algebra', 'pe'],
  ['biology', 'pe', 'history', 'algebra'],
  []
];

export const SCHEDULES = { '8a': SCHEDULE_8A, '8b': SCHEDULE_8B, '7v': SCHEDULE_7V, '4b': SCHEDULE_4B };

export const SCHOOL_SETTINGS = {
  scale: 100,
  periodType: 'quarters',
  days: 7,
  periods: {
    quarters: [
      ['1 четверть', '2026-09-01', '2026-10-25'], ['Осенние каникулы', '2026-10-26', '2026-11-03'],
      ['2 четверть', '2026-11-04', '2026-12-27'], ['Зимние каникулы', '2026-12-28', '2027-01-10'],
      ['3 четверть', '2027-01-11', '2027-03-21'], ['Весенние каникулы', '2027-03-22', '2027-03-29'],
      ['4 четверть', '2027-03-30', '2027-05-26'], ['Летние каникулы', '2027-05-27', '2027-08-31']
    ],
    trimesters: [
      ['1 триместр', '2026-09-01', '2026-11-29'], ['Осенние каникулы', '2026-11-30', '2026-12-06'],
      ['2 триместр', '2026-12-07', '2027-02-28'], ['Зимние каникулы', '2027-03-01', '2027-03-08'],
      ['3 триместр', '2027-03-09', '2027-05-26'], ['Летние каникулы', '2027-05-27', '2027-08-31']
    ],
    halves: [
      ['1 полугодие', '2026-09-01', '2026-12-27'], ['Зимние каникулы', '2026-12-28', '2027-01-10'],
      ['2 полугодие', '2027-01-11', '2027-05-26'], ['Летние каникулы', '2027-05-27', '2027-08-31']
    ]
  }
};
