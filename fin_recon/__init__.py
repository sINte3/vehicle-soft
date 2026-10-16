# -*- coding: utf-8 -*-
"""fin_recon: неизменяемые снимки финансовой сверки операторов дронов.

FIN-RECON-STORAGE-001. Месячная сверка «книги диспетчеров ↔ начисления ↔
записи о выдаче ↔ Vehicle Soft ↔ DJI» готовится вне программы и приходит
сюда ОДНИМ приватным пакетом: `snapshot.json` с фиксированной схемой,
`manifest.json`, `SHA256SUMS.txt` и неизменённые оригиналы, адресованные по
SHA-256. Пакет проверяется целиком до транзакции, снимок кладётся в
таблицы `fin_recon_*` одной транзакцией и больше не меняется.

Пакет не входит в Flask-приложение: не импортирует ни `app`, ни `models`.
`app = create_app()` вызывает `db.create_all()` на импорте, и инструмент,
импортирующий приложение, стал бы писателем схемы. Таблицы создаёт только
миграция FIN_RECON_STORAGE_001; экран читает их отдельным соединением
`mode=ro`, как экраны agro-work и полей DJI.

Правила, которые пакет держит конструкцией, а не обещанием:

- неизвестное — это NULL с доказательным типом UNKNOWN, а не 0: контракт
  отвергает число при UNKNOWN и пустоту при любом другом типе;
- снимок не утверждается и не закрывается: статус единственный —
  RECONCILED_WITH_EXCEPTIONS, поля утверждения пусты, и триггер базы не даёт
  вставить их непустыми;
- историю не переписывает никто: UPDATE и DELETE запрещены триггерами на
  всех таблицах `fin_recon_*`, новая редакция — только версия N+1 со ссылкой
  на хеш версии N;
- существующие таблицы (`drone_works`, `drone_flights`, `dji_*`) пакет не
  читает и не пишет: снимок — отдельный ограниченный контекст.

Числа конкретного месяца в коде не живут. Контрольные ожидания месяца лежат
в его приватном пакете (снимок объявляет их сам, импортёр пересчитывает и
сверяет).
"""

SNAPSHOT_SCHEMA = 'fin-recon-snapshot/1'
PACKAGE_SCHEMA = 'fin-recon-package/1'
EXPECTATIONS_SCHEMA = 'fin-recon-expectations/1'
MIGRATION_ID = 'FIN_RECON_STORAGE_001'
IMPORTER_VERSION = 'fin-recon-importer/1'

# [REASON]: единственный статус этого макроэтапа. «Финансово закрыт» и
# «касса подтверждена» владелец не разрешал; если такой статус появится, он
# придёт отдельной миграцией вместе с отдельной записью утверждения, а не
# правкой строки снимка.
STATUS_RECONCILED_WITH_EXCEPTIONS = 'RECONCILED_WITH_EXCEPTIONS'
STATUSES = (STATUS_RECONCILED_WITH_EXCEPTIONS,)

# Доказательный тип значения. UNKNOWN -- значение NULL, и только оно.
EVIDENCE_BOOK_TEXT = 'BOOK_TEXT'                 # записано текстом в книге
EVIDENCE_BOOK_NUMBER = 'BOOK_NUMBER'             # число, введённое в ячейку книги
EVIDENCE_BOOK_FORMULA = 'BOOK_FORMULA'           # расчёт формулой книги
EVIDENCE_REGISTER_CALC = 'REGISTER_CALC'         # расчёт реестра сверки
EVIDENCE_OWNER = 'OWNER_INTERPRETATION'          # трактовка владельца
EVIDENCE_SYSTEM_EXPORT = 'SYSTEM_EXPORT'         # выгрузка Vehicle Soft / экрана DJI
EVIDENCE_UNKNOWN = 'UNKNOWN'                     # неизвестно: значение NULL
EVIDENCE_KINDS = (EVIDENCE_BOOK_TEXT, EVIDENCE_BOOK_NUMBER,
                  EVIDENCE_BOOK_FORMULA, EVIDENCE_REGISTER_CALC,
                  EVIDENCE_OWNER, EVIDENCE_SYSTEM_EXPORT, EVIDENCE_UNKNOWN)

# Смысл числа. Разные величины одной размерности не складываются друг с
# другом: гектары книги, DJI RAW и принятая DJI -- три разных факта.
SEMANTIC_KINDS = (
    'COUNT',
    'BOOK_AREA',             # гектары работ по книгам диспетчеров
    'DB_AREA',               # гектары строк Vehicle Soft
    'DJI_RAW',               # площадь, полученная от DJI
    'DJI_ACCEPTED',          # техническая принятая площадь DJI (не billable)
    'DJI_EXCLUDED',          # подтверждённо исключено из DJI RAW
    'DJI_UNATTRIBUTED',      # DJI без однозначного оператора
    'DJI_ATTRIBUTED',        # DJI с одним кандидатом-оператором
    'BILLABLE_AREA',         # площадь к оплате заказчиком (аудитом не заполняется)
    'SERVICE_AMOUNT',        # стоимость услуги заказчику
    'SERVICE_TARIFF',        # тариф услуги за гектар
    'SALARY_ACCRUAL',        # начисление зарплаты оператору
    'SALARY_RATE',           # ставка зарплаты
    'PAYOUT_TEXT',           # запись о выдаче текстом книги
    'PAYOUT_OWNER',          # выдача, признанная трактовкой владельца
    'PAYOUT_RECOGNIZED',     # всего признано выданным
    'DIFFERENCE',            # арифметическая разница
    'SECONDARY_SUMMARY',     # число вторичного (общего) свода
)

UNITS = ('ha', 'UZS', 'count', 'flights')

# Блок книги: наличный расчёт или справка (счёт заказчику).
BLOCK_CASH = 'CASH'
BLOCK_SPRAVKA = 'SPRAVKA'
BLOCKS = (BLOCK_CASH, BLOCK_SPRAVKA)

# Входит ли строка работы в итог начисления оператора по книге.
SALARY_INCLUDED = 'INCLUDED_IN_BOOK_ACCRUAL'
SALARY_UNKNOWN = 'NOT_ACCRUED_UNKNOWN'
SALARY_STATUSES = (SALARY_INCLUDED, SALARY_UNKNOWN)

# Сверка строки книги с копией Vehicle Soft.
VS_MATCH = 'MATCH'
VS_EXPECTED = 'EXPECTED'
VS_UNEXPECTED = 'UNEXPECTED'
VS_VERDICTS = (VS_MATCH, VS_EXPECTED, VS_UNEXPECTED)

# Запись о выдаче: текст книги или признание владельцем.
PAYOUT_BOOK_TEXT = 'BOOK_TEXT_RECORD'
PAYOUT_OWNER_RECOGNIZED = 'OWNER_RECOGNIZED'
PAYOUT_KINDS = (PAYOUT_BOOK_TEXT, PAYOUT_OWNER_RECOGNIZED)
# [REASON]: вид записи определяет её доказательный тип однозначно. Признание
# владельцем никогда не показывается как «записано текстом», и наоборот.
PAYOUT_EVIDENCE = {PAYOUT_BOOK_TEXT: EVIDENCE_BOOK_TEXT,
                   PAYOUT_OWNER_RECOGNIZED: EVIDENCE_OWNER}

# [REASON]: статусы аномалий хранятся ДОСЛОВНО, как в реестре: «не
# переименовывать статусы произвольно». Код рядом -- только для фильтра
# экрана; новый статус означает новую версию контракта, а не тихий пятый.
ANOMALY_STATUSES = {
    'ОТКРЫТО': 'OPEN',
    'НА КОНТРОЛЕ': 'MONITORED',
    'УЧТЕНО': 'ACCOUNTED',
    'РЕШЕНИЕ ВЛАДЕЛЬЦА': 'OWNER_DECISION',
}
ANOMALY_PRIORITIES = ('P0', 'P1', 'P2')

DECISION_OWNER = 'OWNER_DECISION'
DECISION_CONTROL_NOTE = 'CONTROL_NOTE'
DECISION_KINDS = (DECISION_OWNER, DECISION_CONTROL_NOTE)

SOURCE_ROLES = (
    'BOOK_PRIMARY',          # первичная книга подразделения
    'BOOK_SECONDARY',        # вторичный общий свод
    'BOOK_LOCK_FILE',        # служебный файл блокировки Excel (~$), не книга
    'BOOKS_ARCHIVE',         # архив книг в том виде, как его передал владелец
    'REGISTER_XLSX',         # канонический реестр месяца
    'REGISTER_MD',           # пояснительная записка реестра
    'OWNER_CLARIFICATIONS',  # решения и пояснения владельца
    'DJI_EXPORT_BEFORE',     # экспорт «Контроля площади DJI» до решений
    'DJI_EXPORT_AFTER',      # экспорт «Контроля площади DJI» после решений
    'DJI_REPORT',            # записка о проверке решений DJI
    'VS_EXPORT',             # архив технической сверки с копией Vehicle Soft
    'VS_CONSOLE',            # вывод консоли шагов выгрузки и сравнения
    'AUDIT_MODEL',           # модель аудита (адреса ячеек, разбор дат)
    'HANDOFF',               # передача контекста
    'EVIDENCE_NOTE',         # прочие записки доказательной базы
    'TASK',                  # задание
    'BUNDLE',                # комплект, в котором всё пришло
    'TOOL',                  # инструмент, собравший снимок
    'EXTERNAL',              # известный файл вне пакета (только хеш/размер)
)

MONTH_RE = r'^\d{4}-(0[1-9]|1[0-2])$'
DATE_RE = r'^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$'
SHA256_RE = r'^[0-9a-f]{64}$'
KEY_RE = r'^[A-Z][A-Z0-9_]{0,19}$'
