// Дорожная карта зимы 2026/27 в Word. Запуск: node build_roadmap_docx.js roadmap_ru.docx
// Содержание дублирует 04_roadmap.md; при правках менять оба.
const fs = require('fs');
const { Document, Packer, Paragraph, TextRun, HeadingLevel, Table, TableRow, TableCell, WidthType,
        AlignmentType, LevelFormat, ShadingType, BorderStyle, VerticalAlign } = require('docx');

const FONT = 'Arial';
const BORDER = { style: BorderStyle.SINGLE, size: 4, color: '999999' };
const BORDERS = { top: BORDER, bottom: BORDER, left: BORDER, right: BORDER };

function p(text, opts = {}) {
  // text: string or array of {t, b}
  const runs = (Array.isArray(text) ? text : [{ t: text }]).map(r => new TextRun({ text: r.t, bold: !!r.b, font: FONT, size: opts.size || 22 }));
  return new Paragraph({ children: runs, spacing: { after: opts.after === undefined ? 120 : opts.after, line: 276 }, ...(opts.numbering ? { numbering: opts.numbering } : {}) });
}
function h1(text) { return new Paragraph({ text, heading: HeadingLevel.HEADING_1, spacing: { before: 120, after: 160 } }); }
function h2(text) { return new Paragraph({ text, heading: HeadingLevel.HEADING_2, spacing: { before: 280, after: 120 } }); }
function bullet(text) { return p(text, { numbering: { reference: 'bullets', level: 0 }, after: 80 }); }
function num(text) { return p(text, { numbering: { reference: 'numbers', level: 0 }, after: 100 }); }

function cell(text, width, header = false) {
  return new TableCell({
    width: { size: width, type: WidthType.DXA },
    borders: BORDERS,
    verticalAlign: VerticalAlign.TOP,
    margins: { top: 60, bottom: 60, left: 90, right: 90 },
    shading: header ? { type: ShadingType.CLEAR, fill: 'E8EEF7', color: 'auto' } : undefined,
    children: [new Paragraph({ children: [new TextRun({ text, bold: header, font: FONT, size: 19 })], spacing: { after: 0, line: 252 } })],
  });
}

const widths = [1500, 4238, 1700, 2200]; // = 9638 DXA, ширина текста при полях 2 см на A4
const rows = [
  ['Когда', 'Что делаем', 'Кто', 'Чем заканчивается'],
  ['Ноябрь, 1–2 неделя', 'Пробные полёты на своём поле: замер подачи карбамида и селитры, хронометраж круга, часы налёта за день, лотковый тест ровности. Тест семян саксаула и изеня в баке.', 'Руководитель дронов, пилот, агроном', 'Измеренные подача, круг, часы; пересчёт модели; решение «идём / не идём» по подкормке'],
  ['Ноябрь, 1–2 неделя', 'Звонки: Агентство по лесам (1010), хокимият (65 222-22-22) — контакт Бухарского гослесхозяйства. Письма с предложением показательного посева 20–50 га за свой счёт: гослесхозяйство, облуправление сельского хозяйства (программа пастбищ), проект ФАО в Жондоре.', 'Владелец, руководитель дронов', 'Три встречи'],
  ['Ноябрь', 'Регистрация поставщиком на Агропортале и на xarid.uzex.uz. Проверка в Узавиации, какие территории покрывают наши разрешения.', 'Юрист, бухгалтерия', 'Допуск к госзаказу'],
  ['Ноябрь', 'Осенняя гербицидная обработка озимых своим заказчикам по 200 000 сум/га — предложить всем, кому опрыскивали хлопчатник.', 'Диспетчер, экипажи', 'Первые зимние гектары'],
  ['Ноябрь–декабрь', 'Показательный посев у лесхоза или на площадке ФАО в сезон осенних посадок (в Бухаре с 5 ноября).', '1–2 борта', 'Отчёт с картой вылетов; договор на зиму или отказ с причиной'],
  ['Декабрь', 'Печать КП по измеренным числам. Договоры на февраль–март с осенними заказчиками: подкормка по тоннам, гербициды по гектарам. Заявки на лоты лесхозов и программы пастбищ.', 'Владелец, диспетчер', 'Подписанный портфель на февраль–март'],
  ['Декабрь–январь', 'Внесение гранулированных удобрений в своих садах холдинга (кроны без листвы) и опыт искореняющей обработки сада с агрономом — только на своих садах.', 'Экипаж 1 борта, агроном', 'Решение по садам'],
  ['Январь', 'Докупка разбрасывателей под подписанные объёмы (1 200–1 500 USD за штуку). Оснастка загрузки: воронка, подставка под бак, мешки по 45 кг. Прогрев батарей в машине.', 'Руководитель дронов', 'Техника готова'],
  ['Февраль — середина марта', 'Подкормка озимых по тоннам, гербициды по гектарам. Аэросев по договорам лесхозов и пастбищ в тёплые дни.', '5 и более бортов', 'Выручка межсезонья'],
  ['Март–апрель', 'Саранча по субподряду, если ставка сметы покрывает себестоимость. Старт основного сезона.', 'Все борта', '—'],
];
const table = new Table({
  width: { size: 9638, type: WidthType.DXA },
  columnWidths: widths,
  rows: rows.map((r, i) => new TableRow({ tableHeader: i === 0, cantSplit: true, children: r.map((c, j) => cell(c, widths[j], i === 0)) })),
});

const doc = new Document({
  creator: 'Buxoro Agroklaster',
  title: 'План и дорожная карта: чем занять дроны с ноября по март',
  styles: {
    default: { document: { run: { font: FONT, size: 22 } } },
    paragraphStyles: [
      { id: 'Heading1', name: 'Heading 1', basedOn: 'Normal', next: 'Normal', quickFormat: true, run: { font: FONT, size: 32, bold: true, color: '000000' }, paragraph: { spacing: { before: 120, after: 160 }, outlineLevel: 0 } },
      { id: 'Heading2', name: 'Heading 2', basedOn: 'Normal', next: 'Normal', quickFormat: true, run: { font: FONT, size: 26, bold: true, color: '000000' }, paragraph: { spacing: { before: 280, after: 120 }, outlineLevel: 1 } },
    ],
  },
  numbering: { config: [
    { reference: 'bullets', levels: [{ level: 0, format: LevelFormat.BULLET, text: '•', alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 567, hanging: 283 } } } }] },
    { reference: 'numbers', levels: [{ level: 0, format: LevelFormat.DECIMAL, text: '%1.', alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 567, hanging: 340 } } } }] },
  ] },
  sections: [{
    properties: { page: { margin: { top: 1134, bottom: 1134, left: 1134, right: 1134 } } },
    children: [
      h1('План и дорожная карта: чем занять дроны с ноября по март'),
      p([{ t: 'Дата: 30.10.2026. ' }, { t: 'План действий по итогам исследования зимнего разбрасывания удобрений (отчёт, международный опыт, госпрограммы). ' },
         { t: 'Исходные условия: 15 дронов DJI Agras T40 и один бак-разбрасыватель; в феврале 2026 работ не было, в марте — 1 340 га на 11 бортов; контакта с лесным агентством нет; холдинг готов работать по госзаказам; свои сады есть.' }]),
      h2('Решения по цене, которые предлагаю принять'),
      num([{ t: 'Удобрения считаем по тоннам, а не по гектарам. ', b: true }, { t: 'Себестоимость у нас пропорциональна килограммам: борт за день вносит одни и те же 1,8 т при любой норме. Фермер проверяет тонны по мешкам. Оператор не может ни завысить тонны (их привёз фермер), ни выбрать «ступень» в отчёте.' }]),
      num([{ t: 'Один тариф — 1 800 000 сум за тонну ', b: true }, { t: 'любого гранулированного удобрения при нормах от 100 кг/га. Минимальный заказ — 2 тонны за выезд (3,6 млн сум). Без скидок и ступеней: скидка — только решением владельца по конкретному договору, не оператором.' }]),
      num([{ t: 'Приёмка по мешкам, акт в двух подписях. ', b: true }, { t: 'Мешки пересчитываются с представителем фермера при приёмке и по окончании; в акт пишутся тонны. Данные датчика дрона и карта вылетов из Vehicle Soft — второй контроль: тонны по акту и по датчику должны сходиться в пределах 5 %, гектары по карте — с нормой агронома в пределах 10 %. Если датчик не передал данные — считаем по акту и ставим флаг «вылет без массы», как сегодня флаг «гектары без телеметрии».' }]),
      num([{ t: 'Семена — за гектар, опрыскивание — за гектар ', b: true }, { t: '(как сейчас, 200 000 сум/га). Это три отдельные услуги с одной ценой каждая.' }]),
      h2('Дорожная карта'),
      table,
      p('', { after: 60 }),
      h2('Что считать успехом зимы 2026/27'),
      bullet('Пробные полёты проведены, числа в модели заменены измеренными.'),
      bullet('Хотя бы один договор с лесхозом или программой пастбищ, пусть на 50 га.'),
      bullet('5 бортов заняты в феврале–марте не меньше 20 дней каждый.'),
      bullet('Себестоимость тонны по факту не выше 1,4 млн сум; иначе пересматривать тариф или отказываться от подкормки в пользу семян и гербицидов.'),
      h2('Чего не делать'),
      bullet('Не печатать КП с ценой до пробных полётов.'),
      bullet('Не давать операторам право скидки и не вводить ступени тарифа.'),
      bullet('Не работать селитрой без ежевечерней промывки техники.'),
      bullet('Не входить в лоты госзаказа, не узнав ставку сметы.'),
      bullet('Не считать зимнюю работу вторым сезоном: это заполнение простоя.'),
    ],
  }],
});

Packer.toBuffer(doc).then(buf => { fs.writeFileSync(process.argv[2], buf); console.log('written ' + process.argv[2] + ' ' + buf.length + ' bytes'); });
