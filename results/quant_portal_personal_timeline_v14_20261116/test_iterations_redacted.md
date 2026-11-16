# v1.4 checks and corrections

- The initial readback audit exposed loss of microsecond precision in API timestamps. Its first receipt was overwritten before archiving; the exact initial mismatch count is therefore not claimed. The serialized API now preserves full precision, and the frozen private snapshot audit checks saved quote and news timestamps.
- An inherited v1.3 raw-response count assertion still assumed its earlier retention policy. It was revised to permit the v1.4 recent-curve provenance rule; the v1.3 scenario and its visible-cell checks remain in the suite.
- The first Django test command used the system Python and failed because `django` was absent there. The existing pinned portal virtual environment ran the final Django checks successfully. Both command outputs remain as local logs.
- The first 390px browser view crowded the chart controls. CSS was changed to a one-column control layout, then the narrow viewport was reopened. The visual receipt records this correction.

Final counts are the executed commands in `python_tests.log` and `django_tests_final.log`. The Python and Django suites cover different entry points but should not be added as independent experimental observations. The browser check, synthetic tests and real private-cache audit are separate evidence types.
