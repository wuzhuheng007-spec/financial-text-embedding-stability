"""Request spacing. The provider limits an account to a number of requests per second (0.2 for an account that never
bought credits); on 2026-11-23 the second of two back-to-back preflight requests was answered 429. Spacing is a wait
before a request. It is never a second attempt: a 429 still stops the run."""
import pytest

from scripts.x_stance_v1 import config, provider


class Clock:
    def __init__(self):
        self.t, self.sleeps = 100.0, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(round(s, 3))
        self.t += s


def _transport(interval, clock, took=1.0, fail_on=None):
    t = provider.UrllibTransport("k" * 20, min_interval=interval, sleep=clock.sleep, clock=clock.now)

    def send(params):
        t.calls += 1
        clock.t += took
        if fail_on == t.calls:
            raise provider.ProviderHTTPError(429, "RATE_LIMITED")
        return {"tweets": []}

    t._send = send
    return t


def test_requests_are_spaced_from_the_end_of_the_previous_one():
    c = Clock()
    t = _transport(6, c)
    for _ in range(3):
        t.get({})
    assert c.sleeps == [6.0, 6.0] and t.calls == 3 and t.waited_seconds == 12.0


def test_no_wait_when_enough_time_already_passed_or_spacing_is_off():
    c = Clock()
    t = _transport(6, c)
    t.get({})
    c.t += 30
    t.get({})
    assert c.sleeps == []
    c2 = Clock()
    t2 = _transport(0, c2)
    t2.get({}), t2.get({})
    assert c2.sleeps == []


def test_a_429_is_not_retried_and_the_next_request_still_waits():
    c = Clock()
    t = _transport(6, c, fail_on=2)
    t.get({})
    with pytest.raises(provider.ProviderHTTPError):
        t.get({})
    assert t.calls == 2                                         # one attempt, no retry
    t.get({})
    assert c.sleeps == [6.0, 6.0] and t.calls == 3


def test_the_live_transport_takes_its_spacing_from_the_config():
    cfg = config.load()
    assert cfg["acquisition"]["min_request_interval_seconds"] >= 5.5      # documented free-tier limit: one per 5 s
    assert provider.live_transport(cfg, "k" * 20).min_interval == cfg["acquisition"]["min_request_interval_seconds"]
