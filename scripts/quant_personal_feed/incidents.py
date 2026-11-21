"""Sanitized incident records for the collector.

Before 2026-11-21 a failed provider loop left only an exception class name in `provider_state`. The reconnect loop of
2026-11-20 (a retention query failing with "database or disk is full") therefore had to be reconstructed from outside.
An incident record says when, which provider, in which connection phase, a reason code, and where in the code it was
raised - and nothing that could identify the account or reproduce supplier content.

Exception TEXT is untrusted, whatever the exception class. `OSError.strerror` is filled in by the caller, an
AssertionError or an application exception can wrap a received frame, and a sqlite3 error can be constructed by hand
with any message. So a record never contains exception text. What it keeps is structured and comes from the
interpreter or the engine, not from the message:

  * operating-system errors: the errno NAME and the platform's own description of that number (`os.strerror`);
  * SQLite errors: the engine's numeric result code and its standard name;
  * every other class: the class name and a fixed placeholder.

The traceback is file, line and function only - no source line, no local variable. `record()` never raises: writing
a record must not be able to break the quote connection it describes.
"""
from __future__ import annotations

import errno
import json
import os
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
OMITTED = "Exception message omitted"

_URL = re.compile(r"\b([a-z][a-z0-9+.-]*)://([^\s/?#'\"<>]*)([^\s?#'\"<>]*)(\?[^\s#'\"<>]*)?(#[^\s'\"<>]*)?", re.I)
_BEARER = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{6,}|\b(Token)\s+[A-Za-z0-9._~+/=-]{16,}", re.I)
_PAIR = re.compile(r"(?i)\b([\w.-]*(?:token|secret|password|passwd|api[_-]?key|apikey|authorization|cookie|session|signature|sign|key|code|credential)[\w.-]*)(\"?\s*[=:]\s*\"?)([^\s\"',;&}]+)")
_LONG = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_\-+/=]{24,}(?![A-Za-z0-9])")

# SQLite primary result codes (the low byte of an extended code) -> standard name and the reason kind used below.
_SQLITE = {1: ("SQLITE_ERROR", "OTHER"), 5: ("SQLITE_BUSY", "LOCKED"), 6: ("SQLITE_LOCKED", "LOCKED"), 7: ("SQLITE_NOMEM", "OTHER"),
           8: ("SQLITE_READONLY", "READONLY"), 10: ("SQLITE_IOERR", "IO"), 11: ("SQLITE_CORRUPT", "CORRUPT"), 13: ("SQLITE_FULL", "DISK_OR_TEMP_FULL"),
           14: ("SQLITE_CANTOPEN", "CANTOPEN"), 19: ("SQLITE_CONSTRAINT", "CONSTRAINT"), 26: ("SQLITE_NOTADB", "CORRUPT")}


def sanitize_text(text: object) -> str:
    """Remove anything that looks like a credential or a parameterised URL, then cut to MAX_MESSAGE characters.
    Used only on text this module wrote itself; it is not what makes an exception message safe (nothing does)."""
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


def _os_errno(exc: BaseException):
    number = getattr(exc, "errno", None)
    return number if type(number) is int and number in errno.errorcode else None


def _sqlite_code(exc: BaseException):
    number = getattr(exc, "sqlite_errorcode", None)
    return number if type(number) is int and number >= 0 else None


def safe_message(exc: BaseException) -> str:
    """Canonical structured metadata only. The exception's own text is never read: not `str(exc)`, not `strerror`,
    not `args`. (Reading it could also raise, if `__str__` is broken.)"""
    if isinstance(exc, sqlite3.Error):
        code = _sqlite_code(exc)
        if code is None:
            return "SQLITE_ERROR (no engine code; message omitted)"
        return f"{_SQLITE.get(code & 255, ('SQLITE_ERROR', 'OTHER'))[0]} (code {code})"
    if isinstance(exc, OSError):
        number = _os_errno(exc)
        if number is None:
            return "OS_ERROR (no errno; message omitted)"
        return f"{errno.errorcode[number]}: {os.strerror(number)}"
    return OMITTED


def reason_code(exc: BaseException) -> str:
    """A code from the exception's TYPE and structured fields. The message text is not consulted."""
    name = type(exc).__name__
    if name == "AuthorizationError":
        return "AUTHORIZATION"
    if name == "QuotaError":
        return "SUBSCRIPTION_QUOTA"
    if isinstance(exc, sqlite3.Error):
        code = _sqlite_code(exc)
        return "STORE_SQLITE_" + (_SQLITE.get(code & 255, ("", "OTHER"))[1] if code is not None else "UNCODED")
    if isinstance(exc, TimeoutError):
        return "TIMEOUT"
    if isinstance(exc, ConnectionError):
        return "NETWORK_" + re.sub(r"(?<!^)(?=[A-Z])", "_", re.sub(r"[^A-Za-z0-9]", "", name)).upper()[:48]
    if isinstance(exc, OSError):
        number = _os_errno(exc)
        return "OS_" + (errno.errorcode[number] if number is not None else "ERROR")
    return "UNCLASSIFIED_" + re.sub(r"[^A-Za-z0-9]", "", name)[:40].upper()


def safe_traceback(exc: BaseException, limit: int = 12) -> list[str]:
    """`file:line in function`, innermost last. Paths are relative to the repository or reduced to the package tail.
    Uses frame metadata only (no source lines are looked up, no locals are read)."""
    out = []
    tb = exc.__traceback__; frames = []
    while tb is not None:
        frames.append((tb.tb_frame.f_code.co_filename, tb.tb_lineno, tb.tb_frame.f_code.co_name)); tb = tb.tb_next
    for filename, lineno, func in frames[-limit:]:
        p = Path(filename)
        try:
            shown = p.resolve().relative_to(ROOT).as_posix()
        except (ValueError, OSError):
            shown = "/".join(p.parts[-3:]) if "site-packages" in p.parts or "lib" in [x.lower() for x in p.parts] else p.name
        out.append(f"{shown}:{lineno} in {re.sub(r'[^A-Za-z0-9_<>.]', '', func)[:80]}")
    return out


def describe(exc: BaseException, *, provider: str, phase: str, recorded_state: str, attempt: int | None = None) -> dict:
    if phase not in PHASES:
        phase = "THREAD"
    cause = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    return {"kind": "INCIDENT", "at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "provider": str(provider)[:40], "phase": phase,
            "recorded_state": str(recorded_state)[:40], "error_class": re.sub(r"[^A-Za-z0-9_.]", "", type(exc).__name__)[:80], "reason_code": reason_code(exc),
            "message": safe_message(exc), "os_errno": _os_errno(exc) if isinstance(exc, OSError) and not isinstance(exc, sqlite3.Error) else None,
            "sqlite_code": _sqlite_code(exc) if isinstance(exc, sqlite3.Error) else None,
            "caused_by": re.sub(r"[^A-Za-z0-9_.]", "", type(cause).__name__)[:80] if cause else None, "attempt": attempt, "traceback": safe_traceback(exc)}


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
            "os_errno": None, "sqlite_code": None, "caused_by": None, "attempt": failures, "outage_seconds": seconds, "first_failure_at_utc": since_utc,
            "traceback": []}


def emit(record: dict, store=None) -> None:
    """One JSON line on stderr (the container log) and, when a store is given, one row in `provider_incidents`.
    Each sink is tried on its own; neither failure reaches the caller."""
    try:
        print("personal_feed " + json.dumps(record, ensure_ascii=True, sort_keys=True), file=sys.stderr, flush=True)
    except Exception:
        pass
    if store is not None:
        try:
            store.record_incident(record)
        except Exception:
            pass


def record(exc: BaseException, store=None, **where) -> dict | None:
    """describe + emit, never raising. Returns the record, or None when even describing failed (then a minimal
    line with the class name only is attempted on stderr)."""
    try:
        rec = describe(exc, **where)
    except Exception:
        try:
            print('personal_feed {"kind": "INCIDENT", "reason_code": "RECORDING_FAILED", "error_class": "%s"}'
                  % re.sub(r"[^A-Za-z0-9_.]", "", type(exc).__name__)[:80], file=sys.stderr, flush=True)
        except Exception:
            pass
        return None
    emit(rec, store)
    return rec
