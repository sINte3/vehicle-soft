# Leaflet 1.9.4 — положен в репозиторий, без CDN

Первая внешняя фронтенд-библиотека проекта. Согласие владельца — 28.09.2026
(«Карта — поддерживаю Leaflet»), запись в `docs/tracks/gps-plan-fakt.md`,
раздел «Карта: одна на всю программу». Подключается ТОЛЬКО страницами с картой
через общий компонент `static/js/vs-map.js`; в базовый шаблон не входит.

| Что | Значение |
|---|---|
| Версия | 1.9.4 (последняя стабильная ветки 1.x) |
| Источник | npm-пакет `leaflet@1.9.4`, `https://registry.npmjs.org/leaflet/-/leaflet-1.9.4.tgz` |
| sha1 архива | `23fae724e282fa25745aff82ca4d394748db7d8d` — совпал с `dist.shasum` реестра |
| sha512 архива | `nxS1ynzJOmOlHp+iL3FyWqK89GtNL8U8rvlMOsQdTTssxZwCXh8N2NB3GDQOL+YR3XnWyZAxwQixURb+FA74PA==` — совпал с `dist.integrity` реестра |
| sha256 `leaflet.js` | `db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a` |
| sha256 `leaflet.css` | `a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6` |
| Лицензия | BSD 2-Clause, файл `LICENSE` рядом |
| Что взято | `dist/leaflet.js`, `dist/leaflet.css`, `dist/images/*.png`, `LICENSE` — побайтно, без правок |
| Что не взято | исходники и карты исходников (`*.map`): в консоли разработчика будет одно предупреждение о `leaflet.js.map`, на работу это не влияет |

Файлы не правятся руками: правка чужой библиотеки теряется при первом
обновлении, а сверка по суммам выше перестаёт работать. Всё своё — в
`static/js/vs-map.js` и `static/css/vs-map.css`.

Обновление — только целиком: скачать архив новой версии из реестра npm,
сверить `dist.shasum`/`dist.integrity`, заменить четыре пункта из таблицы,
переписать суммы здесь и прогнать `tools/ux/check_gps_map.mjs` на стенде
`tools/ux/serve_gps_fact.py`.
