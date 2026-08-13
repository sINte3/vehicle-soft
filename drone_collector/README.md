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

## The constraint that is not negotiable

**Nothing in a URL may be edited after the site has signed it.**

DJI signs every API request with a `Signature` header produced by its own
bundled WASM code. This was tested against the live site on 2026-07-31:

* a request without the `Signature` header is answered with body `code 101`;
* a genuine request whose query string is altered afterwards — `page_size=30`
  rewritten to `page_size=50` — is also answered with body `code 101`. The
  signature covers the query string, so an edit fails for being an edit: `50`
  is a size the site itself offers and it is rejected all the same.

> The 2026-07-31 test actually substituted `page_size=100`. It is written here
> with a real size instead, because **DJI offers no page size above 50** and a
> `100` in an example reads as a value that can be obtained. The same `100`
> still appears in the module docstring of `browser.py`; correcting it there is
> a code change and is deliberately not made in this documentation-only pass.

So the requests have to be issued by the site itself, and we only listen to the
responses. That is the whole reason a browser is involved. It follows that:

* the reporting period cannot be injected into the URL — it is set through the
  site's own date picker, and then **verified** against the URL the site
  produced;
* pagination is by page number, through the site's own control.

Do not attempt to generate, replay or reverse-engineer the `Signature` header.
It has been tested and it does not work.

**But note what that test does not prove.** It proves an *altered* URL is
rejected, not that the values in it are fixed. `page_size` is a per-session
setting with its own control on the page; when that control is used, the app
signs a request carrying the new value. The control was opened on the live
page on 2026-07-31 and offers exactly **`10 / page`, `20 / page`, `30 / page`,
`50 / page`** — **50 is the maximum, there is no 100**. The collector drives
that control and only that control: it logs the option list on every run
(so a change on DJI's side is visible rather than assumed), selects the
largest, and reads the size actually in force back out of an intercepted URL.

**`serial_number` is not a machine identifier.** It is unique per flight
(verified on 10 385 records), so it is a second flight id. Nothing keys,
groups or deduplicates on it; deduplication is on `id` alone, and a test walks
the AST of every module to keep it that way.

**Success is not the HTTP status, and the success code is not 200.** Every
response from the DJI API is HTTP 200; the truth is the `code` field inside
the JSON body, and the envelope looks like this:

```
success:  {"status": 200, "code": 0,   "message": "OK", "data": [...]}
failure:  {"status": 101, "code": 101}   missing or invalidated signature
          {"status": 408, "code": 408}   bad timestamp
```

`status` is 200 on every success whatever the code; on a failure `status` and
`code` are equal to each other. **Success is `code == 0`.** This cost the first
live run: the collector had the success code as 200, rejected every good page
with `Rejected flight-list response (code-0)`, and exited 4. Any code that
decides success from `response.status` — or from `code == 200` — is wrong.

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

**A period crossing a year boundary is split automatically.** The SmartFarm
calendar resets such a range, so the collector requests one calendar year at a
time — see "Historical collection" below. Nothing needs doing about it on the
command line.

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
| 6 | A window captured **zero flights**. | Nothing was sent. Usually the session is in the wrong region. If the period really is empty, set `DJI_ALLOW_EMPTY_WINDOW=true`. |
| 7 | The session is in the **wrong region**. | The log shows the expected and the observed value. Re-run `--save-session` and check the region selector. |

Exits 6 and 7 both exist for one failure: a session switched to another region
returns **zero rows with no error at all**, so the run looks perfectly
successful and collects nothing. Exit 7 is the fast diagnosis and needs a
selector that has never been confirmed. Exit 6 needs no selector and is the
one that actually protects the data — which is why an empty window is a
failure by default rather than a quiet success.

Exit 3 deserves its own sentence, because it is the one that looks like an
over-reaction. After the dates are typed into the picker, the collector reads
`filters[timestamp_gteq]` and `filters[timestamp_lteq]` back out of the request
URL the site produced and compares them against the intended window, with one
hour of slack on each edge. On a mismatch it logs both values, fails, and sends
nothing. A collector that silently harvests the wrong period is worse than one
that fails: the flights it brings back are real, they land in the database, and
nothing downstream can tell that the window was not the one that was asked for.

---

## Capture flights per device (DRONE-BODYCODE-001)

A second entry point, `drone_collector.devices`. It **sends nothing** — it
reads the site and writes files.

```powershell
cd C:\transport-report
& "C:\Program Files\Python314\python.exe" -m drone_collector.devices --from 2025-09-01 --to 2025-09-30 --out C:\qa\dji_sept
```

Why it exists. A flight is attributed to a machine by its DJI **nickname**,
and nicknames migrated between airframes: the aircraft called `14 Servis`
today was `PeshkShodi` on 27.09.2025 and `15 Servis` two days later. For
October 2025 and March 2026 the mapping was recovered by matching monthly
totals, and that is already fixed. September 2025 has no such solution — one
spelling was carried by two different airframes inside the month — so the link
has to be **read** rather than derived. The site's own **Device** filter reads
it: a device is identified by its serial, which renaming does not touch.

The sweep sets the period once, walks the window unfiltered to get a control
count, then for every device the filter offers: applies the filter, walks its
pages, and records each flight's `id` next to the device name. That `id` is
`drone_flights.dji_flight_id`, so the attribution becomes a fact that was
read, not a number that was matched.

Output in `--out`:

| File | What is in it |
|---|---|
| `flights_by_device.csv` | `dji_flight_id;device;nickname;started_utc;area_ha` |
| `summary.json` | per device: flights, pages, whether the walk completed; plus any flight seen under two devices |
| `raw/<device>.json` | the response bodies verbatim, so the run can be re-parsed without visiting the site again |

Extra exit codes: **8** — the Device dropdown produced no options, so the
filter selectors need correcting (the panel markup is in the log); **9** — the
sweep finished but the devices do not add up to the unfiltered control, or one
flight came back under two devices. In both cases the files are written and
the data must not be trusted until the log explains the difference.

`--list-devices` prints the device names and exits. `--device "1 Klaster"`
(repeatable) sweeps only those; the control check is then skipped, because a
subset is not expected to add up to the whole window.

**The filter selectors were written without the live page.** The saved DOM of
2026-07-31 never had the filter panel open, so the constants at the top of
`devices.py` are candidates, not confirmed readings. The first run logs the
panel's markup — correct the constants there, in one place, and nowhere else.

---

## Logs

Rotating file log in `drone_collector/logs/collector.log` (5 MB per file, 10
backups) plus stdout. Every run ends with one structured line:

```
RUN SUMMARY kind=incremental dry_run=false period_from=2026-07-02 period_to=2026-07-31
  windows=1 windows_completed=1 region=not-found page_size=50
  pages=25 pages_expected=25 flights_captured=1219 flights_deduped=1217
  self_duplicates=2 rejected_responses=0 batches=2 seen=1217 new=214
  duplicates=886 unresolved=3 errors=0 exit=0
```

`region` is `ok`, `not-found` or `skipped` — see exit 7 above; `not-found` is
the expected value today. `page_size` is the size actually in force, read back
from an intercepted request URL rather than from the control's label.

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

The suite is plain `unittest` and needs nothing beyond the standard library —
the command above works in the application's Python and in the collector's
virtual environment alike. **If you prefer to run it with `pytest`, install
pytest first** (`pip install pytest`, in the same virtual environment): it is
deliberately absent from `requirements.txt`, because the collector does not
need it to run and the service must not carry a test dependency.

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

**Confirmed by the live run of 2026-07-31**, which collected end to end:

* `SELECTOR_RANGE_INPUTS` — `.ant-picker-range .ant-picker-input input` matches
  the live markup. The inputs are `readonly` while the panel is shut; the
  click-then-type path works and the `fill()` fallback was never needed.
* `SELECTOR_PAGINATION_NEXT` / `..._ENABLED` — the walk paginated correctly and
  stopped cleanly on the disabled control.
* `SELECTOR_PAGE_SIZE_CHANGER` and its option list — opened, read, logged, and
  the largest option selected. The collector noticed the pagination had
  renumbered and restarted the walk, exactly as designed.
* Period verification matched **to the millisecond** on three separate windows.
* The half-applied period is real, not theoretical: the site issues a request
  while the second date is still being typed. Both that page and the
  pre-period page were captured and correctly discarded.
* Year-boundary splitting works: `2025-12-01..2025-12-31` and
  `2026-01-01..2026-01-31` each verified independently, with no overlap in
  flight ids between them.
* **Numbers.** 1–7 July 2026 gave 1217 flights across 25 pages; the site's own
  tile for the same period reports 1217 flights, 31 154 L and 0 kg. The
  collector's area came to 1318.58 ha against the tile's 1318.62 — the tile
  labels hectares as `mu`, a known trap of this interface, and the residual is
  rounding.
* The list payload carries all twenty fields the ingest endpoint reads,
  including `lat`, `lng` and `work_time_seconds`, so **no per-flight detail
  request is needed**. `geometry_md5` is *not* in the list payload; it exists
  only on the single-flight endpoint.

**Still unverified, and the reason each is safe:**

* `SELECTOR_COOKIE_ACCEPT` and `SELECTOR_LIST_VIEW` — both best-effort. A miss
  is logged and the run continues; the list toggle is only attempted when
  nothing was captured at all.
* `SELECTOR_REGION_INDICATOR` matches **nothing** on the live page, and that is
  the honest state of it: the page has no region display, only an "Other
  Regions" switcher whose class is a build-hashed CSS-module name. That item is
  deliberately not matched — a selector picking it up would compare the
  expected region against the string "Other Regions" and block every run. So
  the region check reports `not-found` and warns until someone identifies the
  real element. Exit 6 is what protects the run meanwhile.

**The collector has run against the live cabinet and has POSTed for real.**
On 2026-07-31:

* `DRONE_API_TOKEN` was set at machine level and the service picked it up after
  `Restart-Service`;
* the window 2026-07-01..2026-07-07 was sent for real — **1217 flights**,
  1318.58 ha, matching the cabinet's own tile for the same period;
* re-running that same window deduplicated as designed: `new 0`,
  `duplicates 1217`;
* the historical backfill 2025-08-01..2026-07-30 brought **28 832 flights with
  0 errors**.

What is still not done is **scheduling**: the collector has never run
unattended. See "Deployment" below.

## Historical collection

**Year boundaries are handled for you.** The picker resets a range that crosses
one, so `--from 2025-06-30 --to 2026-07-31` is split automatically into
`2025-06-30 … 2025-12-31` and `2026-01-01 … 2026-07-31`, and each is a full
cycle of its own: set the period, verify it, walk it, send it. If one window
fails, the ones already sent are kept and the log names both lists — what
completed and what still needs collecting. Re-running a completed window is
harmless; the ingest deduplicates by DJI flight id.

**The cabinet keeps roughly a rolling 12 months.** Established on 2026-07-31,
not assumed: the earliest selectable date moves forward. Every day the
collector does not run is a day of history that falls off the far end, which
makes a regular run a condition of keeping the data rather than a convenience.

**Sizing.** Observed on the live cabinet on 2026-07-31: 2026-01-01 →
2026-07-31 is 705 pages at 30 per page (21 123 flights). The historical window
2025-08-01 → 2026-07-30 was then collected for real: **28 832 flights**, which
at the maximum page size of 50 is roughly **580 pages**. `DJI_MAX_PAGES=2000`
therefore leaves ample headroom — the old 500 would have terminated that
legitimate backfill as if it had run away.

At `DJI_SETTLE_MS=2500`, a several-hundred-page walk takes tens of minutes of
clicking. That is expected, not a hang.

**Leave `DJI_SETTLE_MS` at its default of 2500.** The value of 8000 that
circulated for a while was a workaround for `DRONE-PERIOD-RACE-001`, not a
setting: period verification used to accept or reject on whatever had been
captured last, and the range picker's intermediate request — fired with the
start date applied and the end date still stale — could win that race and fail
the run with exit code 3. Verification now waits for a capture whose own URL
carries the requested period, so the workaround is unnecessary. It was also
expensive: `paginate()` waits `settle_ms` after every click, so 8000 cost eight
seconds *per page* and roughly doubled every run.

---

## Deployment

Deliberately **not** wired into the application's service configuration or
scheduler. Scheduling it is a separate, owner-run step.

Both halves are proved as of 2026-07-31: the browser side collected end to end,
and the sending side delivered 1217 flights for one week and 28 832 for the
historical window with 0 errors. `DRONE_API_TOKEN` is set at machine level on
production.

What remains is scheduling, and it is not a service under `LocalSystem`:
Playwright installs Chromium into the user profile, so the task has to run
under the account that ran `playwright install`, or headless. Nothing schedules
the collector today — every run so far has been started by hand.

The collector performs no unit conversion. `new_work_area` is m², `spray_usage`
is millilitres and `sow_usage` is grams in the payload; `drones.py` performs
those conversions at ingest. The flight objects are forwarded verbatim.
