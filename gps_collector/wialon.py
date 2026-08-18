# -*- coding: utf-8 -*-
"""The Wialon transport of the collector. Standard library only.

PORTED, NOT REWRITTEN. Every guard below was paid for by a failed run of
tools/wialon_probe6_fleet_health.py and is carried over unchanged in behaviour:

  * one request per two seconds (decision G9);
  * a hard deadline on a whole request, because urlopen's timeout is per
    socket operation and a peer that dribbles bytes never trips it;
  * three retries with growing pauses -- a single dropped connection across the
    public internet used to kill a forty-minute run;
  * errors 1003/1004/1005 ("wait") are not failures: they pause and are
    retried by the caller, not counted as lost data;
  * a rolling two-minute message budget, half the documented ceiling;
  * messages/unload after every load, so the server does not hold our last
    result while we ask for the next.

READ-ONLY. Services used: token/login, core/search_items,
messages/load_interval, messages/unload, core/logout. No create, update or
delete anywhere, by construction: there is no method here that could.
"""

import json
import threading
import time
import urllib.parse
import urllib.request

from . import config


class WialonError(Exception):
    """A service answered with an error code."""

    def __init__(self, code):
        self.code = code
        super().__init__("error %s (%s)" % (code, config.ERRORS.get(code, "unknown")))


def err_code(result):
    if isinstance(result, dict) and "error" in result:
        return result["error"]
    return None


def describe(result):
    code = err_code(result)
    if code is None:
        return None
    return "error %s (%s)" % (code, config.ERRORS.get(code, "unknown"))


class Client:
    """One logged-in session against the Wialon server.

    The counters are part of the contract, not debugging leftovers: acceptance
    criterion 3 of GPS-1 is "no repeated logins over a nightly run", and a
    claim like that is only worth something if something counts.
    """

    def __init__(self, pause=None, log=print):
        self.pause = config.PAUSE_S if pause is None else max(0.0, pause)
        self.log = log
        self.sid = None
        self.requests = 0
        self.logins = 0
        self.abandoned = 0
        self._last_call_at = 0.0
        self._messages_window = []

    # --- pacing -------------------------------------------------------------

    def _pace(self):
        """Hold the agreed request rate, whatever the server answers."""
        waiting = self.pause - (time.monotonic() - self._last_call_at)
        if waiting > 0:
            time.sleep(waiting)
        self._last_call_at = time.monotonic()

    def _message_budget(self, about_to_load):
        while True:
            edge = time.monotonic() - config.MESSAGE_WINDOW_S
            while self._messages_window and self._messages_window[0][0] < edge:
                self._messages_window.pop(0)
            loaded = sum(count for _, count in self._messages_window)
            if loaded + about_to_load <= config.MESSAGE_WINDOW_LIMIT:
                return
            self.log("    limit soobshcheniy blizko (%d za 2 min), pauza 5 s"
                     % loaded)
            time.sleep(5.0)

    def _note_messages(self, count):
        self._messages_window.append((time.monotonic(), count))

    # --- one request --------------------------------------------------------

    @staticmethod
    def _fetch(query, box):
        try:
            request = urllib.request.Request(
                config.AJAX,
                data=urllib.parse.urlencode(query).encode("utf-8"),
                headers={"Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(request, timeout=config.TIMEOUT) as response:
                box["raw"] = response.read().decode("utf-8", "replace")
        except Exception as exc:                                   # noqa: BLE001
            box["error"] = exc

    def call(self, svc, params, with_sid=True):
        """One service call, paced, deadlined and retried. Raises on failure."""
        query = {"svc": svc, "params": json.dumps(params, ensure_ascii=False)}
        if with_sid and self.sid:
            query["sid"] = self.sid
        last = None
        for attempt in range(config.RETRIES):
            self._pace()
            self.requests += 1
            box = {}
            worker = threading.Thread(target=self._fetch, args=(query, box),
                                      daemon=True)
            worker.start()
            worker.join(config.HARD_DEADLINE_S)
            if worker.is_alive():
                # [REASON]: the thread is a daemon and dies with the process.
                # Abandoning a stuck request is what keeps a nightly run moving;
                # the run of 16.08 stood at one object for a whole day because
                # nothing here gave up.
                self.abandoned += 1
                self.log("    otvet ne prishel za %d s, brosayu zapros (%s)"
                         % (int(config.HARD_DEADLINE_S), svc))
                last = TimeoutError("no answer in %d s"
                                    % int(config.HARD_DEADLINE_S))
            elif "error" in box:
                last = box["error"]
                if attempt + 1 < config.RETRIES:
                    self.log("    svyaz propala (%s), povtor cherez %d s"
                             % (type(last).__name__, config.RETRY_PAUSE_S[attempt]))
            else:
                raw = box.get("raw", "")
                try:
                    return json.loads(raw)
                except ValueError:
                    return {"_unparsed": raw[:500]}
            if attempt + 1 < config.RETRIES:
                time.sleep(config.RETRY_PAUSE_S[attempt])
        raise last

    # --- services -----------------------------------------------------------

    def login(self, token):
        answer = self.call("token/login", {"token": token}, with_sid=False)
        code = err_code(answer)
        if code is not None:
            raise WialonError(code)
        self.sid = answer.get("eid")
        self.logins += 1
        return self.sid

    def logout(self):
        if not self.sid:
            return
        try:
            self.call("core/logout", {})
        except Exception:                                          # noqa: BLE001
            pass
        self.sid = None

    def list_units(self):
        """Every object with its name and the time of its last message.

        One request for the whole fleet, and no messages loaded at all -- the
        same shape tools/wialon_last_seen.py uses. `last_t` is None for an
        object that has never reported: never-reported and long-silent are
        different facts and are kept apart here as they are there.
        """
        answer = self.call("core/search_items", {
            "spec": {"itemsType": "avl_unit", "propName": "sys_name",
                     "propValueMask": "*", "sortType": "sys_name"},
            "force": 1, "flags": config.FLAG_NAME_AND_LAST_POSITION,
            "from": 0, "to": 0})
        code = err_code(answer)
        if code is not None:
            raise WialonError(code)
        units = []
        for item in (answer.get("items") or []):
            if not item.get("id"):
                continue
            position = item.get("pos") or {}
            units.append({"id": int(item["id"]), "name": item.get("nm") or "",
                          "last_t": position.get("t")})
        return units

    def load_interval(self, unit_id, t_from, t_to,
                      limit=config.MAX_MESSAGES_PER_INTERVAL):
        """Messages of one object over [t_from, t_to). Returns the raw list.

        Error 1001 ("no messages for the interval") is an answer, not a
        failure: it means the object was silent, and the caller must be able to
        record that fact rather than treat the night as lost.
        """
        self._message_budget(limit)
        answer = self.call("messages/load_interval",
                           {"itemId": int(unit_id), "timeFrom": int(t_from),
                            "timeTo": int(t_to), "flags": 0, "flagsMask": 0,
                            "loadCount": int(limit)})
        code = err_code(answer)
        if code == 1001:
            return []
        if code is not None:
            raise WialonError(code)
        messages = answer.get("messages") or []
        self._note_messages(len(messages))
        return messages

    def unload(self):
        """Free the last loaded result on the server. Never fatal."""
        try:
            self.call("messages/unload", {})
        except Exception:                                          # noqa: BLE001
            pass


def positions(messages):
    """(t, lon, lat, speed, course, sats) of the messages that have a fix.

    [REASON]: Wialon calls longitude `x` and latitude `y`. Swapping them
    produces a file that looks perfectly well-formed while putting every field
    in Bukhara off the coast of Somalia, and nothing downstream would say so.
    The names are spelled out here, once, so the swap has one place to happen
    and one place to be tested.
    """
    out = []
    for message in messages:
        position = message.get("pos") or {}
        lon, lat = position.get("x"), position.get("y")
        if lon is None or lat is None:
            continue
        stamp = message.get("t")
        if stamp is None:
            continue
        out.append((int(stamp), float(lon), float(lat),
                    float(position.get("s") or 0),
                    None if position.get("c") is None else int(position["c"]),
                    None if position.get("sc") is None else int(position["sc"])))
    return out
