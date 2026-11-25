"""
Shared HTTP client with exponential backoff.

Every scraper here talks to a third-party endpoint that rate-limits. Without a
retry layer a 429 arrives as an ordinary response whose body parses to zero
results, and callers read that as "no more jobs" — the scan truncates silently
and nothing in the logs says why.

`get`/`post` are drop-in replacements for `requests.get`/`requests.post`: they
return the final Response (so existing `status_code != 200` checks keep
working) and only raise when every attempt raised, exactly as requests does
today. What they add is retrying the statuses that mean "ask again later".
"""
from __future__ import annotations

import random
import threading
import time
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests
from loguru import logger

DEFAULT_TIMEOUT = 15
MAX_RETRIES = 4
BASE_DELAY_SEC = 0.5
MAX_DELAY_SEC = 8.0
MAX_RETRY_AFTER_SEC = 30.0

# Minimum gap between two requests to the same host.
#
# Retrying a 429 is not the same as avoiding one. The LinkedIn guest endpoint
# was being hit ~1,300 times per scan with no gap at all, which earns a block
# within the first minute — after which every retry just extends it. Pacing is
# the fix; the backoff below is only the safety net.
HOST_MIN_INTERVAL: Dict[str, float] = {
    "www.linkedin.com": 2.5,
    "linkedin.com": 2.5,
    # One Render address. Workday limits that address, not each tenant hostname,
    # so intel.wd1 and nvidia.wd5 have to share a queue.
    "myworkdayjobs.com": 4.0,
}
DEFAULT_MIN_INTERVAL = 0.0

# Once a host has rate-limited us this many times in a row (after retries),
# stop asking. Continuing to hammer a limiter is what turns a short throttle
# into a long one, and every one of those requests is time the scan is not
# spending on sources that do work.
RATE_LIMIT_STRIKES = 4
RATE_LIMIT_COOLDOWN_SEC = 600.0


class RateLimited(requests.RequestException):
    """A host is in cooldown after rate-limiting us repeatedly."""


_limiter_lock = threading.Lock()
_last_request_at: Dict[str, float] = {}
_strikes: Dict[str, int] = {}
_cooldown_until: Dict[str, float] = {}
_tripped: Dict[str, int] = {}     # host -> how many times it has tripped


def _host_of(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower()
    except ValueError:
        return ""


def _pace_key(host: str) -> str:
    if host.endswith(".myworkdayjobs.com"):
        return "myworkdayjobs.com"
    return host


def _await_turn(host: str) -> None:
    """Block until this host's minimum interval has elapsed."""
    host = _pace_key(host)
    interval = HOST_MIN_INTERVAL.get(host, DEFAULT_MIN_INTERVAL)
    if not interval:
        return
    while True:
        with _limiter_lock:
            now = time.monotonic()
            ready_at = _last_request_at.get(host, 0.0) + interval
            if now >= ready_at:
                # Claim the slot inside the lock, so parallel workers queue up
                # behind each other instead of all passing the same check.
                _last_request_at[host] = now
                return
            wait = ready_at - now
        time.sleep(min(wait, interval))


def _check_cooldown(host: str) -> None:
    host = _pace_key(host)
    with _limiter_lock:
        until = _cooldown_until.get(host, 0.0)
        if until and time.monotonic() < until:
            remaining = int(until - time.monotonic())
            raise RateLimited(f"{host} is rate-limiting us; backing off for {remaining}s")
        if until:
            _cooldown_until.pop(host, None)
            _strikes[host] = 0


def _note_rate_limited(host: str) -> None:
    host = _pace_key(host)
    with _limiter_lock:
        _strikes[host] = _strikes.get(host, 0) + 1
        if _strikes[host] >= RATE_LIMIT_STRIKES:
            _cooldown_until[host] = time.monotonic() + RATE_LIMIT_COOLDOWN_SEC
            _tripped[host] = _tripped.get(host, 0) + 1
            logger.warning(
                f"{host} rate-limited {_strikes[host]}x in a row — pausing it for "
                f"{int(RATE_LIMIT_COOLDOWN_SEC)}s. This scan will be missing its results."
            )


def _note_ok(host: str) -> None:
    host = _pace_key(host)
    if _strikes.get(host):
        with _limiter_lock:
            _strikes[host] = 0


def rate_limited_hosts() -> List[str]:
    """Hosts that hit the strike limit since the last reset, for reporting."""
    with _limiter_lock:
        return sorted(_tripped)


def reset_rate_limit_state() -> None:
    """Clear limiter state — called at the start of a scan."""
    with _limiter_lock:
        _strikes.clear()
        _cooldown_until.clear()
        _tripped.clear()

# Statuses that mean "transient — ask again", as opposed to 403/404 which mean
# "this answer will not change on a retry".
RETRYABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}

# Scrapers fan out over thread pools, and a requests.Session is not documented
# as thread-safe. One session per thread keeps connection pooling (which
# matters at a dozen workers x hundreds of requests) without sharing state.
_local = threading.local()


def _session() -> requests.Session:
    session = getattr(_local, "session", None)
    if session is None:
        session = requests.Session()
        _local.session = session
    return session


def _retry_after_seconds(response: requests.Response) -> Optional[float]:
    """Honour Retry-After when the server sends a plain number of seconds.

    The HTTP-date form is ignored rather than parsed: it is rare on these
    endpoints, and a misparsed date could stall a scan for hours.
    """
    raw = response.headers.get("Retry-After", "").strip()
    if not raw.isdigit():
        return None
    return min(float(raw), MAX_RETRY_AFTER_SEC)


def _backoff_delay(attempt: int, response: Optional[requests.Response]) -> float:
    if response is not None:
        explicit = _retry_after_seconds(response)
        if explicit is not None:
            return explicit
        if response.status_code == 429:
            delay = min(6.0 * (2 ** attempt), 20.0)
            return delay + random.uniform(0, 1.0)
    delay = min(BASE_DELAY_SEC * (2 ** attempt), MAX_DELAY_SEC)
    return delay + random.uniform(0, 0.5)


def request(
    method: str,
    url: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = MAX_RETRIES,
    fresh: bool = False,
    **kwargs,
) -> requests.Response:
    """Issue a request, retrying transient failures with exponential backoff.

    Returns the last Response received. Raises requests.RequestException only
    if every attempt raised without producing a response.

    fresh=True skips the thread-local Session so guest APIs (LinkedIn JD,
    Workday detail) do not inherit cookies that trigger 429s.
    """
    last_exc: Optional[Exception] = None
    last_response: Optional[requests.Response] = None
    host = _host_of(url)

    # Raises RateLimited if this host is already in cooldown, so a scan stops
    # asking rather than spending its whole run collecting 429s.
    _check_cooldown(host)

    for attempt in range(max_retries + 1):
        _await_turn(host)
        try:
            if fresh:
                response = requests.request(method, url, timeout=timeout, **kwargs)
            else:
                response = _session().request(method, url, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            last_exc = exc
            if attempt == max_retries:
                break
            time.sleep(_backoff_delay(attempt, None))
            continue

        last_response = response
        last_exc = None
        if response.status_code not in RETRYABLE_STATUSES:
            _note_ok(host)
            return response
        # Two retries on a 429. More than that is what lengthens the block.
        limited = response.status_code == 429
        budget = 2 if limited else max_retries
        if attempt >= budget:
            logger.warning(
                f"HTTP {response.status_code} after {attempt + 1} attempts: {url}"
            )
            if response.status_code == 429:
                _note_rate_limited(host)
            return response

        delay = _backoff_delay(attempt, response)
        logger.debug(
            f"HTTP {response.status_code} on {url} — retry "
            f"{attempt + 1}/{budget} in {delay:.1f}s"
        )
        time.sleep(delay)

    if last_response is not None:
        return last_response
    raise last_exc if last_exc else requests.RequestException(f"Request failed: {url}")


def get(url: str, **kwargs) -> requests.Response:
    fresh = bool(kwargs.pop("fresh", False))
    return request("GET", url, fresh=fresh, **kwargs)


def post(url: str, **kwargs) -> requests.Response:
    fresh = bool(kwargs.pop("fresh", False))
    return request("POST", url, fresh=fresh, **kwargs)


def is_rate_limited(response: requests.Response) -> bool:
    """True when a response is a rate-limit/outage that survived every retry."""
    return response.status_code in RETRYABLE_STATUSES
