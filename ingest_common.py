# -*- coding: utf-8 -*-
"""ingest_common.py -- shared helpers for token-authenticated ingestion
endpoints (PHASE1 shared foundation).

Three ingestion endpoints are needed across the project: Topaz fuel
(exists), drone flights (next), Wialon telemetry (later). This module holds
the parts they must not fork: constant-time token verification, the
new/duplicates/errors counter triple, and body-token extraction.

The REFERENCE implementation is _perform_fuel_sync() in fuel_routes.py
(the live Topaz ingestion path). It is deliberately NOT migrated onto this
module -- the risk of touching the live path is not worth the tidiness.
New endpoints build on these helpers; the fuel endpoint stays as it is.

No Flask imports. No database access. Pure functions and one small counter
class -- fully unit-testable (see test_ingest_common.py).
"""

import hmac


def verify_api_token(provided, expected):
    """Constant-time comparison of a submitted token against the expected one.

    Returns False when expected is empty or None -- a missing environment
    variable must DENY, not accept.
    [REASON]: same safe default as FUEL_API_TOKEN handling in the fuel sync:
    an unconfigured token must never behave as "no auth required".

    Both arguments are encoded to bytes before hmac.compare_digest, so
    str/bytes and unicode tokens compare correctly and in constant time.
    """
    if expected is None or expected == '' or expected == b'':
        return False
    if provided is None:
        return False
    if not isinstance(provided, (str, bytes)):
        provided = str(provided)
    if not isinstance(expected, (str, bytes)):
        expected = str(expected)
    if isinstance(provided, str):
        provided = provided.encode('utf-8')
    if isinstance(expected, str):
        expected = expected.encode('utf-8')
    return hmac.compare_digest(provided, expected)


class IngestCounters(object):
    """Counter triple for one ingestion run: new / duplicates / errors.

    [REASON]: the previous drone collector always reported
    records_written == records_seen, which made it impossible to tell from
    the log whether new data had actually arrived. The fuel endpoint
    reports the three outcomes separately; every future collector must too.
    """

    __slots__ = ('new', 'duplicates', 'errors')

    def __init__(self, new=0, duplicates=0, errors=0):
        self.new = new
        self.duplicates = duplicates
        self.errors = errors

    def as_dict(self):
        return {'new': self.new,
                'duplicates': self.duplicates,
                'errors': self.errors}

    def merge(self, other):
        """Add another run's counters into this one (for batched runs).

        Mutates and returns self, so batch loops can chain:
            total.merge(batch_a).merge(batch_b)
        """
        self.new += other.new
        self.duplicates += other.duplicates
        self.errors += other.errors
        return self

    def __repr__(self):
        return ('IngestCounters(new=%d, duplicates=%d, errors=%d)'
                % (self.new, self.duplicates, self.errors))

    def __eq__(self, other):
        if not isinstance(other, IngestCounters):
            return NotImplemented
        return self.as_dict() == other.as_dict()


def extract_token(payload):
    """Read the API token from a JSON body dict.

    Matches the existing convention of _perform_fuel_sync: the token is
    carried in the request BODY under the 'token' key, not in a header.
    Returns None when payload is not a dict or carries no token.
    """
    if not isinstance(payload, dict):
        return None
    return payload.get('token')
