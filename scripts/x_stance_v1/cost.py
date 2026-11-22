"""Cost arithmetic and the spending guard. Decimal only. Estimates, not a bill."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal


class BudgetMissing(RuntimeError):
    """No frozen limit: fail closed, no request."""


class BudgetExhausted(RuntimeError):
    def __init__(self, limit_kind: str):
        super().__init__(limit_kind)
        self.limit_kind = limit_kind


def request_cost(returned: int, rate: Decimal, minimum: Decimal) -> Decimal:
    """One request: max(returned, 1) x rate when the minimum equals one post (the provider's rule); in general the
    larger of the per-post total and the per-request minimum. Duplicates returned again are charged again."""
    if returned < 0:
        raise ValueError("returned must be >= 0")
    return max(Decimal(returned) * rate, minimum)


def monthly_cost(daily_returned_posts: int, rate: Decimal, days: int = 30) -> Decimal:
    return (Decimal(daily_returned_posts) * days * rate).quantize(Decimal("0.01"))


def cost_table(cfg: dict) -> list[dict]:
    days = cfg["cost_scenarios"]["calendar_days_per_month_assumption"]
    rates = {k: Decimal(v) for k, v in cfg["quoted_standard_rates_usd_per_returned_post"].items()}
    return [{"daily_returned_posts": n, "returned_per_period": n * days,
             **{f"{k}_usd": str(monthly_cost(n, r, days)) for k, r in rates.items()}}
            for n in cfg["cost_scenarios"]["daily_returned_posts"]]


class Budget:
    """Reserves the worst case of the next page before it is requested, then books what came back. Limits are
    total USD, USD per UTC day and returned posts per UTC day. Any missing limit raises BudgetMissing."""

    def __init__(self, *, rate, minimum, page_max: int, total_usd, daily_usd, daily_post_cap, spent_total=0, spent_by_day=None, posts_by_day=None):
        if total_usd is None or daily_usd is None or daily_post_cap is None:
            raise BudgetMissing("total_usd, daily_usd and daily_post_cap must all be frozen")
        self.rate, self.minimum, self.page_max = Decimal(str(rate)), Decimal(str(minimum)), int(page_max)
        self.total_usd, self.daily_usd, self.daily_post_cap = Decimal(str(total_usd)), Decimal(str(daily_usd)), int(daily_post_cap)
        if self.total_usd <= 0 or self.daily_usd <= 0 or self.daily_post_cap <= 0:
            raise BudgetMissing("limits must be positive")
        self.spent_total = Decimal(str(spent_total))
        self.spent_by_day = {k: Decimal(str(v)) for k, v in (spent_by_day or {}).items()}
        self.posts_by_day = dict(posts_by_day or {})
        self.requests = 0

    @staticmethod
    def _day(now=None) -> str:
        return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")

    def worst_case_page(self) -> Decimal:
        return request_cost(self.page_max, self.rate, self.minimum)

    def reserve_next_page(self, now=None) -> None:
        day, worst = self._day(now), self.worst_case_page()
        if self.spent_total + worst > self.total_usd:
            raise BudgetExhausted("TOTAL_USD")
        if self.spent_by_day.get(day, Decimal(0)) + worst > self.daily_usd:
            raise BudgetExhausted("DAILY_USD")
        if self.posts_by_day.get(day, 0) + self.page_max > self.daily_post_cap:
            raise BudgetExhausted("DAILY_POST_CAP")

    def charge(self, returned: int, now=None) -> Decimal:
        day, c = self._day(now), request_cost(returned, self.rate, self.minimum)
        self.spent_total += c
        self.spent_by_day[day] = self.spent_by_day.get(day, Decimal(0)) + c
        self.posts_by_day[day] = self.posts_by_day.get(day, 0) + returned
        self.requests += 1
        return c
