"""Sanitized incident records for the collector.

Before 2026-11-21 a failed provider loop left only an exception class name in `provider_state`. The reconnect loop of
2026-11-20 (a retention query failing with "database or disk is full") therefore had to be reconstructed from outside.
An incident record says when, which provider, in which connection phase, a reason code, and where in the code it was
raised - and nothing that could identify the account or reproduce supplier content:

  * no token, cookie, authorization header or key;
  * no URL query string or user-info part;
  * no supplier payload: the exception MESSAGE is kept only for exception classes whose text is produced by this
    program, SQLite or the operating system; for every other class it is replaced by its length;
  * the traceback is file, line and function only - no source line, no local variable.
"""
from __future__ import annotations

import errno
import json
import re
import sqlite3
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PHASES = ("SELECT_SUBSCRIPTIONS", "AUTHORIZE_AND_CONNECT", "SUBSCRIBE_ACK", "RECEIVE", "STORE_QUOTE", "STORE_SESSION_QUOTES",
          "STORE_STATE", "POLL", "THREAD")
MAX_MESSAGE = 240

_URL = re.compile(r"\b([a-z][a-z0-9+.-]*)://([^\s/?#'\"<>]*)([^\s?#'\"<>]*)(\?[^\s#'\"<>]*)?(#[^\s'\"<>]*)?", re.I)
_BEARER = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{6,}|\b(Token)\s+[A-Za-z0-9._~+/=-]{16,}", re.I)
_PAIR = re.compile(r"(?i)\b([\w.-]*(?:token|secret|password|passwd|api[_-]?key|apikey|authorization|cookie|session|signature|sign|key|code|credential)[\w.-]*)(\"?\s*[=:]\s*\"?)([^\s\"',;&}]+)")
_LONG = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_\-+/=]{24,}(?![A-Za-z0-9])")


def sanitize_text(text: object) -> str:
    """Remove anything that looks like a credential or a parameterised URL, then cut to MAX_MESSAGE characters."""
    s = str(text)

    def url(m):
        host = m.group(2).rsplit("@", 1)[-1]                       # drop user-info
        return f"{m.group(1)}://{host}{m.group(3)}" + ("?<redacted>" if m.group(4) else "")
    s = _URL.sub(url, s)
    s = _BEARER.sub(lambda m: f"{m.group(1) or m.group(2)} <redacted>", s)
    s = _PAIR.sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", s)
    s = _LONG.sub("<redacted>", s)
    s = " ".join(s.split())
    return s if len(s) <= MAX_MESSAGE else s[:MAX_MESSAGE] + "…"


def _own_message(exc: BaseException) -> bool:
    """Is the message text produced by this program, SQLite or the OS (so it cannot carry a supplier payload)?"""
    if isinstance(exc, (sqlite3.Error, OSError, TimeoutError, AssertionError)):
        return True
    module = type(exc).__module__ or ""
    return module.startswith("scripts.market_observer_v1.service") or module.startswith("scripts.quant_personal_feed")


def safe_message(exc: BaseException) -> str:
    """The message, only where it cannot carry supplier content.

    An operating-system error keeps its errno text (`strerror`) and nothing else: library code sometimes builds an
    OSError around a received frame. Any kept text that still looks structured (JSON / markup) is dropped as well."""
    raw = str(exc)
    omitted = f"<omitted: {len(raw)} characters of a third-party message>"
    if isinstance(exc, OSError) and not isinstance(exc, sqlite3.Error):
        text = exc.strerror if isinstance(exc.strerror, str) and exc.errno else None
        return sanitize_text(text) if text else omitted
    if not _own_message(exc):
        return omitted
    return omitted if any(mark in raw for mark in ("{", "[", "<")) else sanitize_text(raw)


def reason_code(exc: BaseException) -> str:
    name = type(exc).__name__
    if name == "AuthorizationError":
        return "AUTHORIZATION"
    if name == "QuotaError":
        return "SUBSCRIPTION_QUOTA"
    if isinstance(exc, sqlite3.Error):
        text = str(exc).lower()
        kind = ("DISK_OR_TEMP_FULL" if "full" in text else "LOCKED" if "locked" in text or "busy" in text else
                "READONLY" if "readonly" in text else "CORRUPT" if "malformed" in text or "corrupt" in text else "OTHER")
        return f"STORE_SQLITE_{kind}"
    if isinstance(exc, TimeoutError):
        return "TIMEOUT"
    if isinstance(exc, ConnectionError):
        return "NETWORK_" + re.sub(r"(?<!^)(?=[A-Z])", "_", name).upper()
    if isinstance(exc, OSError):
        return "OS_" + (errno.errorcode.get(exc.errno, "ERROR") if exc.errno else "ERROR")
    return "UNCLASSIFIED_" + re.sub(r"[^A-Za-z0-9]", "", name)[:40].upper()


def safe_traceback(exc: BaseException, limit: int = 12) -> list[str]:
    """`file:line in function`, innermost last. Paths are relative to the repository or reduced to the package tail."""
    out = []
    for frame in traceback.extract_tb(exc.__traceback__)[-limit:]:
        p = Path(frame.filename)
        try:
            shown = p.resolve().relative_to(ROOT).as_posix()
        except (ValueError, OSError):
            shown = "/".join(p.parts[-3:]) if "site-packages" in p.parts or "lib" in [x.lower() for x in p.parts] else p.name
        out.append(f"{shown}:{frame.lineno} in {frame.name}")
    return out


def describe(exc: BaseException, *, provider: str, phase: str, recorded_state: str, attempt: int | None = None) -> dict:
    if phase not in PHASES:
        phase = "THREAD"
    message = safe_message(exc)
    cause = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    return {"kind": "INCIDENT", "at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "provider": provider, "phase": phase,
            "recorded_state": recorded_state, "error_class": type(exc).__name__, "reason_code": reason_code(exc), "message": message,
            "caused_by": type(cause).__name__ if cause else None, "attempt": attempt, "traceback": safe_traceback(exc)}


def recovery(*, provider: str, state: str, since_utc: str | None, failures: int) -> dict:
    now = datetime.now(timezone.utc)
    seconds = None
    if since_utc:
        try:
            seconds = round((now - datetime.fromisoformat(since_utc.replace("Z", "+00:00"))).total_seconds(), 1)
        except ValueError:
            seconds = None
    return {"kind": "RECOVERED", "at_utc": now.isoformat().replace("+00:00", "Z"), "provider": provider, "phase": "AUTHORIZE_AND_CONNECT",
            "recorded_state": state, "error_class": None, "reason_code": "RECOVERED", "message": f"connected again after {failures} failed attempt(s)",
            "caused_by": None, "attempt": failures, "outage_seconds": seconds, "first_failure_at_utc": since_utc, "traceback": []}


def emit(record: dict, store=None) -> None:
    """One JSON line on stderr (the container log) and, when a store is given, one row in `provider_incidents`.
    Recording must never become a failure of its own."""
    try:
        print("personal_feed " + json.dumps(record, ensure_ascii=True, sort_keys=True), file=sys.stderr, flush=True)
    except Exception:
        pass
    if store is not None:
        try:
            store.record_incident(record)
        except Exception:
            pass
