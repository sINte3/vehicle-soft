# -*- coding: utf-8 -*-
"""Один прогон импорта agro-work: справочники, заявки, история, связки.

Порядок и зачем он такой:

1. виды работ `/work-types/` и реестр машин `/transports/` -- заявки на них
   ссылаются, и до заявок они должны быть известны;
2. полный обход `/applications/?ordering=created_at` страницами по 100.
   [REASON]: по возрастанию даты создания новые заявки дописываются в КОНЕЦ
   списка и не сдвигают уже прочитанные страницы; при сортировке по убыванию
   (по умолчанию у них) каждая новая заявка во время обхода сдвигала бы все
   страницы на одну строку. Инкрементальной выгрузки у agro-work нет (B0,
   `updated_at_from` молча игнорируется) -- изменения видны только полным
   обходом и сравнением на нашей стороне;
3. отметка исчезнувших -- только если выгрузка полна;
4. история статусов для новых и изменившихся закрытых заявок, не больше
   `max_history` запросов за прогон, новейшие первыми;
5. связь машин agro-work с нашей техникой по госномеру и связкам владельца.

Каждая страница и каждая история фиксируются своей транзакцией: оборванный
прогон оставляет согласованную базу, и следующий продолжает с того же места.
"""

from collections import Counter

from . import matching, records, store
from .client import ApiError

VERSION = 'agro-work-import-1'

MAX_REASONS = 20


def _results(answer):
    if isinstance(answer, list):
        return answer
    if isinstance(answer, dict) and isinstance(answer.get('results'), list):
        return answer['results']
    raise ApiError(200, 'GET', 'list', 'answer has no results list')


def _note(detail, key, text):
    bucket = detail.setdefault(key, [])
    if len(bucket) < MAX_REASONS:
        bucket.append(text)


def run_import(client, con, max_history, log=print, now=None):
    """Выполнить прогон. Возвращает (id прогона, статус, счётчики, подробности).

    Исключение из клиента (отказ входа, сбой сервера после повторов)
    записывается в журнал прогона со статусом `error` и пробрасывается
    дальше -- инструмент решает, каким кодом выйти.
    """
    when_dt = now or store.utc_now()
    when = store.stamp(when_dt)
    run_id = store.start_run(con, VERSION, when_dt)
    counters = Counter()
    detail = {'by_status': Counter(), 'inactive': 0}
    status = store.RUN_ERROR
    message = None
    try:
        log('work types ...')
        for _, answer in client.pages('/work-types/'):
            for row in _results(answer):
                try:
                    record = records.work_type_record(row)
                except records.Rejected as exc:
                    counters['work_types_rejected'] += 1
                    _note(detail, 'work_type_rejections', str(exc))
                    continue
                counters['work_types_' + store.upsert_work_type(
                    con, record, when, run_id)] += 1
            con.commit()

        log('transports ...')
        fresh = set()
        for _, answer in client.pages('/transports/'):
            for row in _results(answer):
                try:
                    record = records.transport_from_registry(row)
                except records.Rejected as exc:
                    counters['transports_rejected'] += 1
                    _note(detail, 'transport_rejections', str(exc))
                    continue
                outcome = store.upsert_transport(con, record, when, run_id)
                counters['transports_' + outcome] += 1
                if outcome == 'new':
                    fresh.add(record['id'])
            con.commit()

        log('applications ...')
        seen = set()
        api_count = None
        for page, answer in client.pages('/applications/',
                                         {'ordering': 'created_at'}):
            if page == 1 and isinstance(answer, dict) \
                    and isinstance(answer.get('count'), int):
                api_count = answer['count']
            counters['pages'] = page
            for row in _results(answer):
                counters['rows_seen'] += 1
                try:
                    record = records.application_record(row)
                except records.Rejected as exc:
                    counters['rows_rejected'] += 1
                    _note(detail, 'application_rejections', str(exc))
                    continue
                if record['id'] in seen:
                    counters['rows_duplicate'] += 1
                    continue
                seen.add(record['id'])
                transport = records.transport_from_application(row)
                if transport and store.ensure_transport(con, transport, when):
                    fresh.add(transport['id'])
                    counters['transports_from_applications_only'] += 1
                work_type = records.work_type_from_application(row)
                if work_type:
                    store.ensure_work_type(con, work_type, when)
                outcome = store.upsert_application(con, record, run_id, when)
                counters['rows_' + outcome] += 1
                detail['by_status'][record['status']] += 1
                if record['is_active'] == 0:
                    detail['inactive'] += 1
            con.commit()
            if page % 10 == 0:
                log('  page %d, applications %d' % (page, len(seen)))
        counters['api_count'] = api_count

        # [REASON]: выгрузка полна, только если прочитаны все строки, которые
        # сервер назвал в начале, и ни одна не отвергнута и не повторилась.
        # Отвергнутая строка -- это «не смогли прочесть», а не «её нет»;
        # повтор значит, что страницы сдвигались, и какая-то строка могла
        # проскочить. В этих случаях исчезновение не отмечается вовсе.
        complete = (api_count is not None
                    and counters['rows_seen'] >= api_count
                    and counters['rows_rejected'] == 0
                    and counters['rows_duplicate'] == 0)
        if complete:
            counters['rows_gone'] = store.mark_gone(con, run_id, when)
            con.commit()

        queue = store.history_queue(con, max_history)
        log('status history: %d to fetch now ...' % len(queue))
        for number, (app_id, listed_updated_at) in enumerate(queue, start=1):
            try:
                raw_events = client.history(app_id)
            except ApiError as exc:
                if exc.status != 404:
                    raise
                # Заявку удалили между списком и историей: следующий полный
                # прогон это покажет, прогон из-за одной строки не стоит.
                counters['history_failed'] += 1
                continue
            events = []
            for raw in raw_events:
                try:
                    events.append(records.history_event(raw))
                except records.Rejected as exc:
                    counters['history_events_rejected'] += 1
                    _note(detail, 'history_rejections', str(exc))
            store.store_history(con, app_id, listed_updated_at, events, when,
                                run_id)
            con.commit()
            counters['history_fetched'] += 1
            if number % 50 == 0:
                log('  history %d of %d' % (number, len(queue)))
        counters['history_pending'] = store.history_pending_count(con)

        resolution = matching.resolve(store.transport_rows(con),
                                      store.equipment_rows(con),
                                      store.active_links(con))
        counters['links_changed'] = store.apply_resolution(
            con, resolution, when, run_id, fresh=fresh)
        con.commit()
        detail['links'] = store.match_summary(con)
        status = store.RUN_OK if complete else store.RUN_INCOMPLETE
    except Exception as exc:
        # В журнал -- класс и текст нашей ошибки. Текст ApiError собран без
        # секретов; у чужого исключения берётся только имя класса.
        message = str(exc) if isinstance(exc, ApiError) else type(exc).__name__
        raise
    finally:
        counters['requests'] = client.requests
        counters['logins'] = client.logins
        counters['refreshes'] = client.refreshes
        detail['abandoned'] = client.abandoned
        detail['by_status'] = dict(detail['by_status'])
        detail['counters'] = {k: v for k, v in counters.items()
                              if k not in store.RUN_COUNTERS}
        try:
            con.rollback()
        except Exception:                                          # noqa: BLE001
            pass
        store.finish_run(con, run_id, status, counters, message, detail)
    return run_id, status, counters, detail
