"""Software checks for the conservative M9 period classifier.

These small phrases are synthetic software inputs, not research observations.
"""

from scripts.m9_period_evidence import classify


def test_explicit_duration_classes() -> None:
    assert classify("Three Months Ended", "Three Months Ended March 31")[:2] == (
        "quarterly", "explicit_three_month_span"
    )
    assert classify("Six Months Ended", "Six Months Ended December 31")[:2] == (
        "year-to-date", "explicit_six_month_span_in_fiscal_q2"
    )
    assert classify("Twelve Months Ended", "Twelve Months Ended March 31")[:2] == (
        "trailing-twelve-month", "explicit_twelve_month_span"
    )


def test_annual_reference_requires_explicit_10k_context() -> None:
    assert classify("year ended", "Annual Report on Form 10-K for the year ended")[:2] == (
        "annual-reference", "cross_reference_to_10_k"
    )
    assert classify("year ended", "Revenue for the year ended")[:2] == (
        "other/unresolved", "annual_phrase_context_unresolved"
    )


def test_point_in_time_and_future_schedule_are_not_historical_result_intervals() -> None:
    assert classify("as of", "as of March 31")[:2] == (
        "other/unresolved", "point_in_time_not_interval"
    )
    assert classify("Three Months Ending", "expected future recognition: Three Months Ending")[:2] == (
        "quarterly", "future_schedule_not_realized_result"
    )
