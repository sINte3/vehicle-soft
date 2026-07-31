# DRONE-003 — DJI SmartFarm flight collector

A standalone process that opens DJI SmartFarm in a real browser using a saved
session, sets a reporting period, walks every page of the flight list, captures
the responses the site fetches for itself, and POSTs the flights to Vehicle
Soft in batches.

It lives in this repository but is **not part of the Flask application**. It
imports neither `app` nor `models`, it never touches `transport.db`, and it has
its own `requirements.txt` because the application's service must not grow a
Playwright/Chromium dependency. It is expected to run in its own virtual
environment, under a real user account.

---

## Two constraints that are not negotiable

**`page_size` is 30 and cannot be changed. The period cannot be passed in the
URL.**

DJI signs every API request with a `Signature` header produced by its own
bundled WASM code. This was tested against the live site on 2026-07-31:

* a request without the `Signature` header is answered with body `code 101`;
* a genuine request whose query string is altered afterwards — `page_size=30`
  rewritten to `page_size=100` — is also answered with body `code 101`.

So the requests have to be issued by the site itself, and we only listen to the
responses. That is the whole reason a browser is involved. It follows that:

* the page size is whatever the site uses, which is 30;
* the reporting period cannot be injected into the URL — it is set through the
  site's own date picker, and then **verified** against the URL the site
  produced;
* pagination is by page number, through the site's own control.

Do not attempt to generate, replay or reverse-engineer the `Signature` header.
It has been tested and it does not work.

**Success is not the HTTP status.** Every response from the DJI API is HTTP
200. Success or failure is the `code` field inside the JSON body: `code 200`
means data, `code 101` and `code 408` mean rejection. Any code that decides
success from `response.status` is wrong and will treat a rejection as an empty
but successful run.

---

## Install

The collector runs in its own virtual environment, separate from the
application's Python.

```cmd
cd C:\transport-report\drone_collector
"C:\Program Files\Python314\python.exe" -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
copy .env.example .env
notepad .env
```

Fill in `VEHICLE_SOFT_BASE_URL` and `DRONE_API_TOKEN` in `.env`. The token must
be the same value as the `DRONE_API_TOKEN` the Vehicle Soft service runs with.
Never commit `.env`.

Every command below is run from the repository root with the collector's
Python:

```cmd
cd C:\transport-report
drone_collector\.venv\Scripts\python.exe -m drone_collector.main --help
```

> **Chromium and the service account.** Playwright installs Chromium into the
> user profile. A scheduled task running as `LocalSystem` cannot see it — this
> was the single most common failure in the previous collector's logs. Run the
> scheduled task under the same account that ran `playwright install`.

---

## Create the session file

The collector never types credentials. A human signs in once, by hand.

```cmd
drone_collector\.venv\Scripts\python.exe -m drone_collector.main --save-session
```

A browser window opens on the DJI sign-in page. Sign in, wait until the records
page shows flights, then return to the console and press Enter. The session is
written to `drone_collector/data/storage_state.json` (or wherever
`DJI_STORAGE_STATE` points).

While you are there, **check the region selector**. An accidental click on
"Other Regions" switches the account to an empty country, and the collector
then quietly returns zero flights.

The session file is a live login: it is ignored by git and must never be
committed, copied into a ticket, or pasted into a chat. Repeat this step when a
run exits with code 2.

---

## Run a dry run

A dry run collects exactly as a real run does, but writes the result to a file
instead of sending it. It needs no token and no `VEHICLE_SOFT_BASE_URL`.

```cmd
drone_collector\.venv\Scripts\python.exe -m drone_collector.main --dry-run
drone_collector\.venv\Scripts\python.exe -m drone_collector.main --dry-run --from 2026-07-01 --to 2026-07-07
```

The flights land in `drone_collector/out/flights_<from>_<to>.json`. The file is
the ingest body **without** the token: it can be read, diffed and — with a
token added — replayed by hand.

## Run for a fixed period

```cmd
drone_collector\.venv\Scripts\python.exe -m drone_collector.main --from 2026-07-01 --to 2026-07-31
```

With `--from`/`--to` the run is reported to the ingest endpoint as
`kind=backfill`. Override with `--kind backfill|incremental|replay` when
needed.

## Run the rolling window (the scheduled case)

```cmd
drone_collector\.venv\Scripts\python.exe -m drone_collector.main
```

Without dates the collector takes the last `DJI_WINDOW_DAYS` days inclusive of
today, in local time (`DJI_TZ_OFFSET_HOURS`, 5 for this business), and reports
`kind=incremental`.

Re-running the same period is normal and safe: the ingest endpoint
deduplicates by DJI flight id, so a repeat brings back `duplicates`, not double
data. The previous collector failed about one run in twenty on timeouts, so a
repeat is the expected repair, not an incident.

**The period cannot cross a year boundary.** The SmartFarm calendar resets a
range that does, which the verification step below will catch and report as
exit 3. Collect history in per-year windows.

---

## Exit codes

| Code | Meaning | What to do |
|---|---|---|
| 0 | Success. | Nothing. |
| 1 | Configuration error, a bad command line, or an unexpected failure. | The log names the variable or the flag; a crash carries its traceback. Nothing was sent. |
| 2 | Session missing or expired. | Re-run `--save-session`. Nothing was sent. |
| 3 | Period verification failed. | The log shows the intended and the observed period. Nothing was sent — on purpose. |
| 4 | The page walk did not complete. | Flights that were captured *were* sent; re-run the same period to finish it. |
| 5 | The ingest endpoint rejected a batch. | The log carries the HTTP status and the endpoint's message. 401 means the token, 413 means the batch size, 400 means the body. |

Exit 3 deserves its own sentence, because it is the one that looks like an
over-reaction. After the dates are typed into the picker, the collector reads
`filters[timestamp_gteq]` and `filters[timestamp_lteq]` back out of the request
URL the site produced and compares them against the intended window, with one
hour of slack on each edge. On a mismatch it logs both values, fails, and sends
nothing. A collector that silently harvests the wrong period is worse than one
that fails: the flights it brings back are real, they land in the database, and
nothing downstream can tell that the window was not the one that was asked for.

---

## Logs

Rotating file log in `drone_collector/logs/collector.log` (5 MB per file, 10
backups) plus stdout. Every run ends with one structured line:

```
RUN SUMMARY kind=incremental dry_run=false period_from=2026-07-02 period_to=2026-07-31
  pages=37 pages_expected=37 flights_captured=1102 flights_deduped=1100
  self_duplicates=2 rejected_responses=0 batches=3 seen=1100 new=214
  duplicates=886 unresolved=3 errors=0 exit=0
```

`self_duplicates` is what the **collector** removed — the same page captured
twice. `duplicates` is what the **endpoint** reported — flights already in the
database. They answer different questions and are never added together.
`unresolved` is a subset of `new` (flights stored with an unrecognised
nickname), not a separate bucket.

The token, cookies, the contents of the session file and request headers are
never logged. Request URLs of the flight-list API *are* logged, because the
period is read out of them; they carry filter parameters only.

---

## Tests

Fixture-driven: no network, no browser, no database. They run in the
application's Python — the collector's dependencies are not needed, because
every module that uses them imports them lazily.

```cmd
cd C:\transport-report
"C:\Program Files\Python314\python.exe" -m unittest discover -s drone_collector/tests -t .
```

They cover the window arithmetic (month, year and leap-day boundaries; local
midnight rather than UTC midnight at UTC+5), period verification (exact match,
match within tolerance, one-day-off rejected, no filter parameters rejected),
response filtering (list URL accepted, detail URL rejected, `code 101`
rejected), chunking (0, 1, 999, 1000, 1001, 2500 flights) and in-run
deduplication.

---

## Selectors

Every selector is a module-level constant at the top of `browser.py`. When one
turns out to be wrong, correct it there and nowhere else — the log says which
one failed, and a missing pagination control and a missing range picker each
name their constant.

**Checked against a saved DOM of the live `/records/list` page, 2026-07-31:**

* `SELECTOR_RANGE_INPUTS` — confirmed. Two `<input>` elements inside
  `.ant-picker-input` wrappers, placeholders "Start date" and "End date",
  values already in `YYYY-MM-DD` form, which is the format typed in.
* `SELECTOR_PAGINATION_NEXT`, `SELECTOR_PAGINATION_NEXT_ENABLED` — confirmed.
  `<li title="Next Page" class="ant-pagination-next" aria-disabled="false">`,
  and the disabled state really is carried by `aria-disabled`.
* The filter parameters and the epoch convention — confirmed. The live page
  requests `filters%5Btimestamp_gteq%5D=1767207600000` for 2026-01-01, which
  is local midnight at UTC+5 and is exactly what `window.py` computes.

**Not checked, and the reason each one is still safe:**

* `SELECTOR_COOKIE_ACCEPT` and `SELECTOR_LIST_VIEW` — both best-effort. A miss
  is logged and the run continues; the list toggle is only attempted when
  nothing was captured at all.
* The date inputs carry `readonly` while the calendar panel is closed. The
  collector clicks the input first, which is what makes rc-picker drop the
  attribute — but if the site ever sets it permanently, keystrokes would
  change nothing. So the typed value is read back and the run fails loudly
  instead of collecting the site's default period.
* The API path prefix. Matching is on `flight_records?`, not on
  `/api/web/v1/`, so a version bump on DJI's side does not silently produce a
  zero-flight run; a URL that matches the endpoint but not the expected
  version is captured *and* logged as a version change. The page's other
  calls — `aggr?` and `aggr_by_day?`, which carry the same filter parameters —
  do not match either form.

**The collector has never been run against the live cabinet.** The first run
must be a `--dry-run`, watched, with `DJI_HEADLESS=false`.

## Sizing a historical run

Observed on the live cabinet on 2026-07-31: the period 2026-01-01 → 2026-07-31
is **705 pages** at 30 per page (21 123 flights).

Two consequences for a backfill:

* `DJI_MAX_PAGES` defaults to **500** and would stop such a run at page 500,
  which is reported as exit 4. Raise it for historical collection.
* at `DJI_SETTLE_MS=2500` a 705-page walk takes roughly half an hour of
  clicking. That is expected, not a hang.

Collect history in **per-year windows** in any case: the calendar resets a
range that crosses a year boundary.

---

## Deployment

Deliberately **not** wired into the application's service configuration or
scheduler. Scheduling it is a separate, owner-run step, after a first manual
run has proved the selectors on the live cabinet.

The collector performs no unit conversion. `new_work_area` is m², `spray_usage`
is millilitres and `sow_usage` is grams in the payload; `drones.py` performs
those conversions at ingest. The flight objects are forwarded verbatim.
