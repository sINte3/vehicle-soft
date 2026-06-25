# ТЕХНИЧЕСКОЕ ЗАДАНИЕ
## Модуль «Заявки/Наряды» (WORK-ORDER-001)
## Vehicle Soft / Transport Report — Buxoro Agroklastr
### Версия 1.0 | Июнь 2026

---

## 1. Назначение и цель

Модуль «Наряды» — оперативный журнал выполненных работ по технике. Механик
или оператор открывает наряд перед выездом на задание, фиксирует фактический
результат по завершении и закрывает наряд. При закрытии система автоматически
создаёт строку ежедневного отчёта (`DailyRecord`), избавляя от двойного ввода.

**Принцип:** один наряд = одна техника + одно задание + один день.

---

## 2. Границы MVP

### Входит в MVP

| Сценарий | Пример |
|---|---|
| Сельхозтехника на поле (МТЗ, комбайн) | Пахота 10 га у фермера Каримова |
| Работы по договору с контрагентом | Уборка урожая в Варахшо |
| ТО и ремонт техники | Замена масла, регулировка |
| Внутренние работы (перегон, спецзадание) | — |

### Не входит в MVP (остаётся в `daily_entry`)

| Сценарий | Причина исключения |
|---|---|
| Грузовик в межобластной поездке | Цикл > 1 дня, оплата в день разгрузки |
| Техника в долгосрочной аренде | Это статус, а не задача с измеримым результатом |

Оба сценария продолжают обрабатываться через существующую форму ежедневного ввода
без изменений.

---

## 3. Роли и права доступа

### 3.1 Новая роль: `mechanic` (Механик)

Добавить в `models.py` константу `ROLE_MECHANIC = 'mechanic'` и перевод
`'Механик'` / `'Mexanik'` в словарь ROLES.

### 3.2 Матрица прав для нарядов

| Действие | admin | operator | mechanic | viewer |
|---|:---:|:---:|:---:|:---:|
| Просмотр своей организации | ✓ | ✓ | ✓ | ✓ |
| Просмотр всех организаций | ✓ | — | — | — |
| Создать наряд | ✓ | ✓ | ✓ | — |
| Редактировать свой черновик | ✓ | ✓ | ✓ | — |
| Редактировать чужой черновик | ✓ | — | — | — |
| Назначить механика (assigned_to) | ✓ | — | — | — |
| Начать выполнение (`in_progress`) | ✓ | ✓* | ✓* | — |
| Закрыть с фактом (`done`) | ✓ | ✓* | ✓* | — |
| Отменить | ✓ | ✓** | ✓** | — |

\* Только свои назначенные наряды (mechanic) или созданные ими же (operator).  
\*\* Только в статусе `draft`, только свои.

### 3.3 Доступ по организации

Пользователи видят наряды только своих организаций (через `user_organizations`).
Администратор видит все. «Головное предприятие» настраивается через
существующий механизм назначения организаций — специальных флагов не добавляется.

---

## 4. Статусная машина

```
draft ──→ assigned ──→ in_progress ──→ done
  │           │              │
  └─────────────────────────→ cancelled
```

| Статус | RU | UZ | Кто может перевести |
|---|---|---|---|
| `draft` | Черновик | Qoralama | Создатель при создании (авто) |
| `assigned` | Назначен | Tayinlangan | admin: устанавливает `assigned_to` |
| `in_progress` | В работе | Bajarilmoqda | Создатель или назначенный механик |
| `done` | Выполнен | Bajarildi | Создатель или назначенный механик |
| `cancelled` | Отменён | Bekor qilindi | Создатель (только из `draft`); admin (любой незакрытый) |

**Правила переходов:**
- `draft → assigned`: admin указывает `assigned_to`. Если `assigned_to` не нужен —
  оператор/механик может сам перевести в `in_progress`, минуя `assigned`.
- `draft / assigned → in_progress`: не требует назначенного механика.
- Переход в `done` обязателен вместе с указанием `actual_quantity` и `actual_date`.
- При переходе в `done` система создаёт строку `DailyRecord` (см. раздел 7).
- Каждый переход пишется в `work_order_status_history`.

---

## 5. Модель данных

### 5.1 Таблица `work_orders`

```sql
CREATE TABLE work_orders (
    id                INTEGER PRIMARY KEY,
    number            VARCHAR(20) UNIQUE NOT NULL,      -- WO-2026-00001
    organization_id   INTEGER NOT NULL REFERENCES organizations(id),
    equipment_id      INTEGER NOT NULL REFERENCES equipment(id),
    work_type_id      INTEGER REFERENCES work_types(id),   -- nullable, для подтяжки цены
    work_type_text    VARCHAR(200) NOT NULL DEFAULT '',     -- фактическое название работы
    customer_id       INTEGER REFERENCES customers(id),    -- nullable
    customer_text     VARCHAR(300) NOT NULL DEFAULT '',    -- фактическое имя заказчика
    assigned_to       INTEGER REFERENCES users(id),        -- nullable
    created_by        INTEGER NOT NULL REFERENCES users(id),
    status            VARCHAR(20) NOT NULL DEFAULT 'draft',
    planned_date      DATE NOT NULL,
    actual_date       DATE,                               -- заполняется при закрытии
    unit              VARCHAR(30) NOT NULL DEFAULT 'га',
    planned_quantity  REAL,
    actual_quantity   REAL,                               -- заполняется при закрытии
    default_price     REAL NOT NULL DEFAULT 0,            -- цена на момент создания (из WorkType)
    price             REAL NOT NULL DEFAULT 0,            -- финальная цена (м.б. изменена оператором)
    payment_type      VARCHAR(20) NOT NULL DEFAULT '',    -- naqd/bank/ichki/boshqa
    note              TEXT NOT NULL DEFAULT '',
    daily_record_id   INTEGER REFERENCES daily_records(id),  -- NULL до закрытия
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    closed_at         DATETIME
);

CREATE INDEX ix_wo_org_status_date ON work_orders(organization_id, status, planned_date);
CREATE INDEX ix_wo_equipment_date  ON work_orders(equipment_id, planned_date);
CREATE INDEX ix_wo_assigned        ON work_orders(assigned_to, status);
```

**Поле `default_price`:** снимается с `work_types.default_price` при создании
наряда. Если пользователь меняет `price` — разница логируется в `work_order_status_history`
как событие `price_override`. Изменение цены видно в карточке наряда.

### 5.2 Таблица `work_order_status_history`

```sql
CREATE TABLE work_order_status_history (
    id             INTEGER PRIMARY KEY,
    work_order_id  INTEGER NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
    event_type     VARCHAR(30) NOT NULL DEFAULT 'status_change',
    -- status_change | price_override | assignment_change | note_added
    old_value      VARCHAR(200),
    new_value      VARCHAR(200) NOT NULL,
    changed_by     INTEGER REFERENCES users(id),
    changed_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    comment        TEXT NOT NULL DEFAULT ''
);
```

### 5.3 Изменения в существующих таблицах

```sql
-- Добавить nullable FK к DailyRecord (обратная ссылка из daily_records на work_order)
ALTER TABLE daily_records ADD COLUMN work_order_id INTEGER
    REFERENCES work_orders(id) ON DELETE SET NULL;
```

> Существующие строки `daily_records` получают `work_order_id = NULL`.
> Поведение существующего ежедневного ввода не изменяется.

---

## 6. Бизнес-логика

### 6.1 Нумерация нарядов

Формат: `WO-{YEAR}-{5-digit-seq}`, пример: `WO-2026-00001`.
Генерируется в Python при создании записи (SELECT MAX + 1 по году, с lock на транзакции).
Повторная генерация не требуется — `number` уникален и назначается один раз.

### 6.2 Ценообразование

При выборе `work_type_id` фронт делает запрос к API и подставляет
`work_type.default_price` → поля `default_price` и `price` (оба изначально одинаковы).
Оператор может изменить `price` вручную — это разрешено.
При сохранении: если `price != default_price`, система записывает в историю
событие `price_override` с полями `old_value=default_price`, `new_value=price`.

### 6.3 Автосоздание DailyRecord при закрытии наряда

Когда наряд переводится в статус `done`:

1. Система ищет, есть ли уже `DailyRecord` для данного `(equipment_id, actual_date)`.
2. Берёт следующий `line_index` для этой пары (MAX + 1, или 0 если записей нет).
3. Создаёт строку `DailyRecord`:

```python
DailyRecord(
    work_date    = work_order.actual_date,
    equipment_id = work_order.equipment_id,
    line_index   = next_line_index,
    status       = 'working',
    work_type    = work_order.work_type_text,
    customer     = work_order.customer_text,
    unit         = work_order.unit,
    quantity     = work_order.actual_quantity,
    price        = work_order.price,
    payment_type = work_order.payment_type,
    note         = f'Наряд {work_order.number}',
    created_by   = current_user.id,
    work_order_id = work_order.id,
)
```

4. Устанавливает `work_order.daily_record_id = new_daily_record.id`.
5. Устанавливает `work_order.closed_at = datetime.utcnow()`.

Если техника на закрываемую дату уже имеет >=1 строки в `daily_records`
(ввод был сделан вручную) — система не блокирует создание ещё одной строки
(оператор может иметь несколько работ за день), но показывает предупреждение:
«Внимание: для этой техники уже есть записи за {дату}».

---

## 7. Flask-маршруты (новый файл `work_orders.py`)

```
GET  /work-orders                   — список нарядов (с фильтрами)
GET  /work-orders/new               — форма создания
POST /work-orders/new               — сохранить новый наряд
GET  /work-orders/<id>              — карточка наряда
GET  /work-orders/<id>/edit         — форма редактирования (только draft)
POST /work-orders/<id>/edit         — сохранить изменения
POST /work-orders/<id>/assign       — назначить механика (только admin)
POST /work-orders/<id>/start        — перевести в in_progress
POST /work-orders/<id>/close        — закрыть: ввести факт и перевести в done
POST /work-orders/<id>/cancel       — отменить

GET  /api/work-orders/work-type-price?work_type_id=N  — вернуть default_price (JSON)
```

Зарегистрировать Blueprint `work_orders_bp` в `app.py`.
Добавить в навигацию `base.html` пункт «Наряды» (между «Запчасти» и «Wialon»).
Обернуть все маршруты в `@login_required`.

---

## 8. Интерфейс

Все страницы — Jinja-шаблоны, mobile-first (`max-width: 480px` → карточки,
`min-width: 769px` → таблица). Стиль — существующая CSS-система проекта.

### 8.1 Список нарядов (`work_orders_list.html`)

**Фильтры (тулбар):** статус (all/draft/assigned/in_progress/done/cancelled),
организация (dropdown, только назначенные), период (дата с–по), техника (поиск).

**Мобильный вид:** карточки с цветовым бейджем статуса, название техники,
плановая дата, тип работы, кнопка перехода в карточку.

**Десктоп:** таблица с колонками: №, Дата, Организация, Техника, Тип работы,
Исполнитель, Статус, Действия.

**Счётчики вверху (как в spare_parts_list):** Всего / В работе / Выполнено сегодня /
Просрочено (плановая дата < сегодня, статус не done/cancelled).

### 8.2 Форма создания/редактирования (`work_order_form.html`)

Поля:
- **Дата** (date-picker, по умолчанию сегодня)
- **Организация** (dropdown, только назначенные пользователю; admin видит все)
- **Техника** (dropdown, фильтруется по выбранной организации — AJAX-запрос)
- **Тип работы** (autocomplete из `work_types`, плюс ввод вручную)
  - При выборе из каталога: автозаполнение `unit` и `price` (AJAX `/api/work-orders/work-type-price`)
- **Заказчик** (autocomplete из `customers`, плюс ввод вручную)
- **Ед. измерения** (текстовое поле, подтянуто из WorkType)
- **Плановый объём** (число)
- **Цена за единицу** (число; если изменена — метка «изменено вручную»)
- **Тип оплаты** (naqd / bank / ichki / boshqa)
- **Примечание** (textarea)
- **Назначить механика** (dropdown из пользователей с ролью mechanic, только admin)

Кнопки: «Сохранить черновик» | «Начать выполнение» (если хочет сразу in_progress).

### 8.3 Карточка наряда (`work_order_detail.html`)

Верх: номер, статус-бейдж, организация, техника, дата.
Основной блок: все поля, плановый vs фактический объём (при done).
Блок действий (кнопки доступны по правам):
- «Начать» → POST /start
- «Закрыть» → ведёт на форму закрытия
- «Редактировать» → /edit (только draft)
- «Отменить» (красная, только draft / admin)

Блок «История»: timeline из `work_order_status_history`.
При наличии `daily_record_id` — ссылка «Запись в ежедневном отчёте».

### 8.4 Форма закрытия (`work_order_close.html`)

Упрощённая форма:
- **Фактическая дата выполнения** (по умолчанию = `planned_date`)
- **Фактический объём** (число, обязательное)
- **Тип оплаты** (если не выбран ранее — обязательный)
- **Примечание**
- Если есть уже `daily_records` за эту дату для данной техники — показать предупреждение

Кнопка «Закрыть наряд» → POST /close → создаёт `DailyRecord` → редирект на карточку.

---

## 9. Уведомления BOT003

Использовать существующую таблицу `bot003_notification_outbox`.

| Событие | Получатель | Текст |
|---|---|---|
| Новый наряд создан (статус не draft) | Все admin | «📋 Новый наряд {WO-N}: {техника}, {тип работы}, {дата}» |
| Наряд назначен механику | `assigned_to` | «🔧 Вам назначен наряд {WO-N}: {тип работы}, {дата}» |
| Наряд закрыт | Создатель + admin | «✅ Наряд {WO-N} выполнен. Факт: {actual_qty} {unit}» |
| Наряд отменён | Создатель | «❌ Наряд {WO-N} отменён» |

Уведомления добавляются в `bot003_notification_outbox` внутри той же транзакции,
что и изменение статуса наряда. Паттерн outbox уже работает.

---

## 10. Обновление дашборда (DASH001)

Добавить новый KPI-блок «Наряды» на главную страницу (`index.html`):

- Всего открытых нарядов (статус не done/cancelled) на сегодня
- Выполнено сегодня
- Просрочено (плановая дата < сегодня, статус in_progress/assigned/draft)
- Ссылка «Перейти к нарядам»

Блок размещается рядом с блоком «Запчасти».

---

## 11. Переводы

Добавить в `translations.py` ключи для обоих языков:

| Ключ | RU | UZ |
|---|---|---|
| `nav_work_orders` | Наряды | Buyurtmalar |
| `wo_new` | Новый наряд | Yangi buyurtma |
| `wo_status_draft` | Черновик | Qoralama |
| `wo_status_assigned` | Назначен | Tayinlangan |
| `wo_status_in_progress` | В работе | Bajarilmoqda |
| `wo_status_done` | Выполнен | Bajarildi |
| `wo_status_cancelled` | Отменён | Bekor qilindi |
| `wo_planned_qty` | Плановый объём | Rejalashtirilgan hajm |
| `wo_actual_qty` | Фактический объём | Haqiqiy hajm |
| `wo_assigned_to` | Исполнитель | Ijrochi |
| `wo_close_action` | Закрыть наряд | Buyurtmani yopish |
| `wo_daily_record_created` | Запись в отчёте создана | Hisobotga yozuv yaratildi |
| `role_mechanic` | Механик | Mexanik |

---

## 12. Миграции

Создать `migrate_work_orders_001.py` (один файл, идемпотентный, регистрируется
в `schema_migrations` как `WORK_ORDERS_001`):

1. Добавить `ROLE_MECHANIC = 'mechanic'` в `models.py` (код-константа, не миграция БД).
2. `CREATE TABLE IF NOT EXISTS work_orders (...)`.
3. `CREATE TABLE IF NOT EXISTS work_order_status_history (...)`.
4. `ALTER TABLE daily_records ADD COLUMN work_order_id INTEGER REFERENCES work_orders(id) ON DELETE SET NULL` — выполнять только если колонка не существует.
5. Создать индексы (`IF NOT EXISTS`).
6. Зарегистрировать `INSERT OR IGNORE INTO schema_migrations`.

Миграция запускается вручную перед запуском сервиса (как все предыдущие).

---

## 13. Критерии приёмки MVP

- [ ] Оператор создаёт черновик наряда, форма правильно фильтрует технику по организации.
- [ ] При выборе типа работы из каталога автоматически подставляются `unit` и `price`.
- [ ] Если `price` изменена — в истории наряда появляется событие `price_override`.
- [ ] Наряд проходит весь цикл: draft → in_progress → done.
- [ ] При закрытии наряда автоматически создаётся строка `DailyRecord`.
- [ ] Созданная строка `DailyRecord` видна в ежедневном отчёте и учитывается в Excel.
- [ ] Если за эту дату/технику уже есть `DailyRecord` — форма закрытия показывает предупреждение, но не блокирует.
- [ ] Оператор A не может редактировать черновик оператора B (получает 403).
- [ ] Admin может редактировать и закрыть любой наряд.
- [ ] Роль `mechanic` отображается в `/admin/users` и назначается администратором.
- [ ] BOT003: при закрытии наряда admin получает уведомление в Telegram.
- [ ] Дашборд: блок «Наряды» показывает корректные счётчики.
- [ ] Навигация и страницы корректно работают в интерфейсе RU и UZ.
- [ ] `py_compile` проходит на всех новых файлах.
- [ ] Staging smoke test: все 6 сервисов работают после деплоя.

---

## 14. Явно НЕ входит в MVP

- Связь нарядов с заявками на запчасти (отдельный модуль, Phase 2).
- Наряды для грузовых поездок и долгосрочной аренды (остаются в daily_entry).
- Гибкая матрица прав (настраиваемые роли через UI) — Post-MVP.
- Telegram Mini App для создания нарядов — Phase 2 (после веб-MVP).
- GPS/Wialon-автоматизация моточасов из наряда — Phase 3.
- Редактирование `DailyRecord` прямо из карточки наряда — Post-MVP.
- Плановые наряды (расписание ТО) — отдельный модуль.

---

## 15. Порядок разработки (фазы внутри MVP)

**Фаза 1 (база):** миграция + модели + маршруты без уведомлений + список + форма создания.
Acceptance: создать наряд, просмотреть список, проверить фильтрацию техники по орг.

**Фаза 2 (статусная машина + закрытие):** переходы статусов + форма закрытия +
автосоздание DailyRecord + history.
Acceptance: пройти полный цикл draft→done, убедиться в создании DailyRecord.

**Фаза 3 (доступ + роли):** роль mechanic + ownership-контроль + тесты прав.
Acceptance: оператор A не может редактировать наряд оператора B.

**Фаза 4 (полировка):** BOT003-уведомления + dashboard KPI + переводы + price_override.
Acceptance: все критерии приёмки из п. 13.

---

*Документ: `docs/ТЗ_Наряды_WORK-ORDER-001.md`*
*Коммитить после финального согласования.*
