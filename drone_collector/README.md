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
| 4 | The walk did not complete. | Flights that were captured *were* sent; re-run the same period to finish it. With `--lands`: fewer contours than DJI's own `totalCount`; what was collected was sent, re-run `--lands`. With `--routes` or `--lands --with-geometry`: at least one batch or one polygon failed; what did succeed is in the outbox, and re-running asks only for what is still missing. |
| 5 | The ingest endpoint rejected a batch. | The log carries the HTTP status and the endpoint's message. 401 means the token, 413 means the batch size, 400 means the body. |
| 6 | A window captured **zero flights**, or `--lands` captured **zero contours**. | Nothing was sent. Usually the session is in the wrong region. If the period really is empty, set `DJI_ALLOW_EMPTY_WINDOW=true`; that variable does not apply to `--lands`. |
| 7 | The session is in the **wrong region**. | The log shows the expected and the observed value. Re-run `--save-session` and check the region selector. |
| 10 | `--routes` only: the cabinet **refused to serve the routes**. | Nothing was collected. Re-run after signing in again; if it repeats with a fresh session, the request made from the page is not being signed the way the cabinet expects — that is a finding, not a breakage. |

Codes **8** and **9** are deliberately absent from this table: they belong to
the other entry point of this package, `python -m drone_collector.devices`
(see «Capture flights per device»). One number with two meanings inside one
package is a code an operator eventually reads wrong, looking at a scheduler
log rather than at the source.

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

## Snapshot the field directory (DRONE-LANDS-001)

```
python -m drone_collector.main --lands --dry-run   # writes out/lands_snapshot.json
python -m drone_collector.main --lands             # collects and sends
```

### Why

Every flight DJI reports carries `plot_name` **empty** — on all 10 385 rows in
the database. The reverse-geocoded address is not a substitute: on 19 and 20
September 2025 two *different* farms both came back as
`Bukhara Region, 500200`, and that is how a day's work was attributed to the
wrong customer. The one place the customer's name exists is **Field
Management**, where the operator typed it on the controller. 5 489 contours as
of 2026-08-18. The cabinet offers no export.

### What it reads

```
POST https://kr-ag2-api.dji.com/ag-plot/api/graphql?name=lands
```

The page signs every request: `content-md5` of the body goes into the string
covered by the `signature` header. Change one character of the GraphQL query —
the `after:` cursor, or `first: 20` — and the signature no longer matches, so
**replaying the request from outside the browser does not work**, and
reproducing DJI's signing would break on their next deploy. So `--lands` does
what the flight walk does: it runs a real browser with the saved session, lets
the page sign its own requests, and listens. Pagination is driven by scrolling
the list, because that is what makes the page ask for the next twenty.

`first: 20, after: "0" → "20" → "40" …` — about **275 requests** for 5 489
contours, roughly **seven minutes**, once.

### The trap this walk has

The same page also fires `graphql?name=**landsCluster**` — the map's circle
counts. Same host, same path, same envelope, HTTP 200 either way. Nothing about
the transport tells them apart. The operation name is therefore matched
**exactly**, on the parsed query string, never as a substring: `'name=lands'`
is a substring of `'name=landsCluster'`. There is a test asserting the cluster
URL is *rejected*, not only that the list URL is accepted.

### Units — the number to get right

Areas arrive in **MU**, not hectares. DJI's own query asks for
`totalArea(unit:MU)`, and **1 hectare = 15 mu exactly**. `44.54096254454096`
is the field the cabinet displays as `2.97 ha`. The conversion happens in
`drones.py`, once, and is asserted against six card values read off the live
cabinet — not against a constant the test made up. Get it wrong and 5 489
contours are all off by a factor of fifteen while looking plausible.

The flight payload uses different units again (m² for `new_work_area`). They
are not the same number and must never become one constant.

### Timestamps

`createdAt` / `updatedAt` arrive in **UTC+08:00** — three hours ahead of the
+05:00 the business reads. A contour drawn at 22:00 Tashkent is stamped 01:00
the *next* day at the source. They are converted to UTC on ingest, like every
other datetime in the database; left alone they would match the wrong day's
flights.

### What is deliberately not fetched

The **polygon** of each field. It sits behind `geometry.storage.signedURL`, a
pre-authenticated link that expires **six hours** after it is issued — 5 489 of
them would be thousands of expiring bearer credentials with a six-hour shelf
life, and they change on every request, so storing them would make a re-run of
unchanged data report 5 489 updates.

They are stripped before anything is stored. What is kept is the stable
`contentMd5` of the geometry (which says whether the polygon changed) and the
**bounding box**, which arrives in the list payload itself. A point-in-box test
on a flight's `lat`/`lng` is what the reconciliation actually needs; the box of
a 2.97 ha field measures about 214 × 197 m, so a flight's coordinate lands in
the right box except where two fields touch.

### Where it lands

`field_contours` with `source = 'dji'`, upserted on `external_id` (the DJI
`uuid`). The endpoint is `POST /drones/api/land_sync`, same token-in-the-body
convention as the flight sync.

Counters partition what was sent:

```
seen = new + updated + unchanged + errors
```

`unchanged` is what makes a re-run readable: the second snapshot of an
untouched directory reports **all unchanged and nothing else**, and any other
number is a real change in the cabinet.

The snapshot **never deactivates** a contour, never touches `customer_id` or
`name_uz`, and never writes to `drone_sync_logs` — that table carries the
flight ingest's invariant and nothing foreign belongs in it. The audit trail of
a snapshot is `field_contours.synced_at`.

### Completeness

Every response states `totalCount`, so the walk **checks** rather than assumes.
Collecting fewer contours than DJI reports is exit code 4: what was collected
has been sent (the ingest upserts, so re-running costs only time), but a
partial directory must not look authoritative — a contour missing from it is
invisible afterwards, it simply never matches anything.

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DJI_FIELDS_URL` | `https://www.djiag.com/mission` | The Field Management page. |
| `DJI_MAX_LAND_PAGES` | `1000` | Runaway guard. 5 489 contours at 20 per page is 275. |

`DJI_STORAGE_STATE`, `DJI_HEADLESS`, `DJI_SETTLE_MS`, `DJI_PAGE_TIMEOUT_MS`,
`VEHICLE_SOFT_BASE_URL`, `DRONE_API_TOKEN` and `DRONE_BATCH_SIZE` are shared
with the flight walk.

### Scrolling, and how it fails

The list panel is found by **geometry and overflow**, not by a CSS selector:
every class name on this page is a build-hashed CSS-module name, and the flight
collector already lost its region indicator to exactly that. The collector logs
which element it chose, once, on the first scroll — so a wrong pick reads as a
wrong pick in the log instead of looking like an empty directory. If no
scrollable panel is found it falls back to a mouse wheel over the left of the
page, and says so.

Five consecutive scrolls with no new page end the walk (an infinite list
re-renders while it fetches and can swallow one). Five, not two: ending a
275-page walk early is expensive.

**A stall is ordinary, and re-running is the fix.** On the first production
run, 2026-08-18, the dry run stopped at **3 040 of 5 489** after five idle
scrolls and exited 4; the very next run walked all **275 pages** and finished
`complete=true`. Nothing was wrong with either — the guard exists precisely so
a stalled walk cannot pass for a finished one. Re-run and read the summary.

### Take the snapshot whole, and soon

Contour records are **re-used and re-dated**: searching `Sarvari ptz` in the
cabinet returns rows stamped 2026-07-03, while `avaz ismatov` still carries
2025-09-20. Whatever is not captured now is not recoverable later. Run
`--lands` once for the whole directory rather than in slices.

---

## Collect routes and full field polygons (DRONE-COVERAGE-001, stage B)

```
python -m drone_collector.main --routes --from 2026-06-05 --to 2026-06-05 --dry-run
python -m drone_collector.main --routes --from 2026-06-05 --to 2026-06-05
python -m drone_collector.main --routes --ids-file ids.txt
python -m drone_collector.main --lands --with-geometry --dry-run
python -m drone_collector.main --lands --with-geometry
```

**Neither of these sends anything to Vehicle Soft.** Both collect into the
on-disk outbox at `DRONE_OUTBOX_DIR` (default `drone_collector/data/outbox`);
the receiving endpoints are stage C. Neither writes a `drone_sync_logs` row,
because neither calls the sender at all — `--routes` does not even require
`DRONE_API_TOKEN`.

### What `--routes` collects, and what it must never be called

The response carries a **geometric route**: a sequence of coordinates. There
is no per-point time, no pump state and no spray state in it — proved on 961
points of the sample, and proved for `data_type=simplified` specifically, not
for every DJI source. So the collected object is a route, a route segment, a
coverage candidate. It is never «work», «treated area» or «confirmed
spraying», and nothing in this package says otherwise.

### How the request is made

The route endpoint takes **flight ids**, not a period — confirmed on live
traffic. `--routes --from/--to` therefore walks the flight list of the period
first (the walk that has been in production since 2026-08-08, read-only here),
learns the ids, and then asks for those routes. `--ids-file` skips the walk.

The request itself is issued **by the page**, in its own JavaScript context,
so that the site signs it if the site signs anything. DJI's signature is not
reproduced — the same rule the flight list and the directory already follow.
Whether the page's own `fetch` carries that signature is **not verified**: the
cabinet is unreachable from the development container. If it does not, DJI
answers with its own code and the run exits **10**, naming the outcome.

### The outbox

One record per JSON file. Written to a `.tmp` in the same directory and moved
into place with `os.replace`, so a reader sees the whole record or nothing.
The file name is the deduplication key — kind, identity and content hash — so
re-running the same period creates no second record. Sent records move to
`sent/`, unreadable ones to `corrupt/`; a torn write leaves a `.tmp` that the
next run sweeps and counts.

No signed link, cookie, token or authorization header can enter it: every
record is checked against a list of markers **and** against the shape of a
signed URL before a byte is written, and a match refuses the record without
echoing the value into the exception.

### `--with-geometry`

Downloads the full polygon of every contour through the signed link in the
directory response. The link is taken from the object already in memory, used
once, and cleared immediately; it reaches no file, no log and no exception —
messages from other libraries are scrubbed of any URL before they are logged.
The download is verified against DJI's own `contentMd5`, hashed again with
sha256, versioned by `contentMd5` (an unchanged contour is not downloaded
twice), and stored as the **whole original `FeatureCollection`** — `funcType`,
`parameters.offset`, `ReferencePoint` and any properties we do not recognise
included.

Every contour ends with exactly one named status: `OK`, `SKIPPED_UNCHANGED`,
`NO_GEOMETRY`, `DOWNLOAD_FAILED`, `TOO_LARGE`, `MD5_MISMATCH`, `UNPARSEABLE`,
`INVALID_GEOMETRY`, `SECRET_IN_PAYLOAD`, `AREA_MISMATCH`. There is no
«processed quietly».

The first pass walks 5 489 contours and takes a while. The signed links live
six hours, so the polygons are fetched **inside** the same directory walk —
there is no later moment at which it could be done from saved data.

### Configuration

| Variable | Default | What it does |
|---|---|---|
| `DRONE_OUTBOX_DIR` | `data/outbox` | where the queue lives |
| `DJI_ROUTE_API_ORIGIN` | `https://kr-ag2-api.dji.com` | host of the route API |
| `DJI_ROUTE_BATCH_SIZE` | `25` | flight ids per route request (capped at 100) |
| `DJI_ROUTE_PAUSE_MS` | `1000` | pause between route batches |
| `DJI_GEOMETRY_PAUSE_MS` | `350` | pause between polygon downloads |

### Reading what was collected

```
python tools/drone_route_semantics_probe.py --outbox drone_collector/data/outbox
```

Reports what the routes say about `mission_uuid`, about candidate task
identifiers among the unrecognised protobuf fields, and about the one-sided
consistency of `new_work_area` with the route. Observations only: it names no
verdict, and it prints identifier values nowhere.

---

## Capture flights per device (DRONE-BODYCODE-001)

A second entry point, `drone_collector.devices`. It **sends nothing** — it
reads the site and writes files.

```cmd
cd C:\transport-report
drone_collector\.venv\Scripts\python.exe -m drone_collector.devices --from 2025-09-01 --to 2025-09-30 --out C:\qa\dji_sept
```

Run it with the **collector's** Python, like every other command in this
file: Playwright and Chromium live in `drone_collector\.venv` and nowhere
else. The application's interpreter has no `playwright` module and the run
would stop on the import.

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
sweep finished but the devices do not add up to the size of the window, or one
flight came back under two devices. In both cases the files are written and
the data must not be trusted until the log explains the difference.

**What the first live run (2026-08-13) corrected.** Four things, all of them
now fixed in `devices.py` and covered by tests:

* the `Filter` button is a **toggle**. The panel was already open from reading
  the device list, a second click closed it, and the device select then timed
  out for 45 s. The panel's state is checked before it is clicked.
* the Device control had to be pinned to its label. Its markup is
  `.ant-form-item` holding both `<span class="label">Device</span>` and the
  `ant-select`; the neighbouring Team/Member field is an `ant-select` too, so
  a selector that is not anchored to the label picks whichever comes first.
* the site answers `code-408` in the middle of a walk and the next-page control
  then disappears from the DOM — once on page 30 of 114, once on page 4. The
  walk now pauses and continues from the page it reached, three attempts.
* the window control no longer costs a full unfiltered walk. It is read from
  the first page's `meta_data`: an explicit total when the site sends one,
  otherwise the bounds implied by the page count. The old control walk was
  114 pages *before* any useful work, and it was where the run kept dying.

**What the second live run (2026-08-13) corrected.** The filter itself proved
to work — `2 Klaster` returned 5 flights and `6 Shofirko` 160, both matching
the cabinet exactly, and the request showed how the site filters:
`filters[product_sn_in][]`, the flight-controller serial. Three more things
came out of it:

* `meta_data` carries **both** `count` (rows on this page, 50) and
  `total_count` (the window, 5661). Taking the first key that merely looked
  like a total picked `count` and declared a 50-flight September. Total keys
  are now tried by name in order of preference, and a candidate that
  contradicts the page count is refused outright.
* the `code-408` also hits a device's **first** request, and waiting for a
  capture then achieves nothing — the page is already in the state where it
  answers nothing. The device is retried after a pause, and the retry
  **reloads** the page and re-applies period and page size, because no amount
  of clicking revives that state.
* a device that still fails no longer takes the run with it. It is recorded,
  the sweep continues, and the log ends with a ready-made command line for
  each machine that has to be picked up separately.

**The sweep is resumable, and that is the point.** Every device is written to
disk the moment it is read — its rows into the CSV, its bodies into `raw/`,
its line into `progress.json`. Re-run the same command and it skips whatever
is already there and carries on; `--restart` sweeps everything again. This
exists because the cabinet gets less willing the more it is asked: four runs
inside half an hour on 2026-08-13 returned 29 pages, then 3 pages, then two
devices, then a `code-408` on the very first request of the page — before any
of this module's code ran. Losing a whole run to that is not acceptable, so
nothing is held in memory to the end any more.

**What the run on a different machine (2026-08-14) settled.** Twelve of the
fifteen devices came back, 4 849 flights, and every one of the twelve matched
the cabinet's own per-device count exactly. Two things came out of it:

* the device name must never be **interpolated into a selector**. The first
  version built `:has-text(...)` with `json.dumps(name)`, which escapes
  Cyrillic to `\uXXXX` — so for `4 Ғиждувон` the selector hunted for a literal
  backslash-u string and timed out after twelve devices had been read.
  Options are now compared as plain strings, exactly, which also rules out
  `8 Garden` selecting `8 GardenU`.
* a clock **202 seconds** off swept twelve devices without a single 408. The
  skew warning threshold was raised accordingly: warning on a working clock is
  a false alarm, and false alarms teach people not to read the log. The skew is
  printed either way.

**When the cabinet refuses everything, check the clock first.** `code-408` is
DJI's own word for **bad timestamp** — the request is signed with the
machine's clock, so a clock that has drifted far enough gets every request
rejected, no matter the selectors, the session or how long you wait. The sweep
now measures it: on the first response it compares DJI's `Date` header against
the local clock and prints either `Clock agrees with DJI within N s` or a
`CLOCK SKEW` error naming the offset. Fix it with `w32tm /resync /force` and
re-run.

Same check by hand, without running anything:

```powershell
(Get-Date).ToUniversalTime().ToString('r')
(Invoke-WebRequest -Uri https://www.djiag.com/ -UseBasicParsing -Method Head).Headers['Date']
```

**`ToUniversalTime()` is not optional.** `Get-Date -Format r` prints LOCAL time
and appends the literal word GMT, so on a UTC+5 machine it reads five hours
off and invents a skew that is not there.

If those two differ by minutes, suspect the clock. If they agree and 408 still comes back, then it is throttling or the
session — wait half an hour, or re-run `--save-session`.

**If a device still will not finish**, sweep it alone —
`--device "3 Gijduvon"` — and repeat for the rest. Each machine is a short
walk of its own, and the files from separate runs can be concatenated: the
CSV carries the device name on every row.

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

### Proved on production, 2026-08-18

The first live snapshot: **275 pages, 5 489 of 5 489 contours, 0 self
duplicates, 0 rejected responses**, `complete=true`; the ingest took all six
batches with `seen=5489 new=5489 updated=0 unchanged=0 errors=0`. The scroll
probe picked `<DIV class='ag-infinite-scroll'>` — the list panel — on the
first try, which is the part that could only be confirmed against the live
page.

---

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
