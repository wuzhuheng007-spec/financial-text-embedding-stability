"""Boundary tests against the FROZEN phase5 program (research/market_sentiment/phase5_strategy_v1/run.py).

The program file is imported as-is (hash-checked against its freeze), never modified. These are synthetic
checks of rule boundaries, not market results.
"""
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "research/market_sentiment/phase5_strategy_v1"
FROZEN_RUN_SHA = "09682c2161fe260d0df41328771368202fb350de5b4d92d68211b85a54604eae"

try:
    import numpy as np
    import pandas as pd
    HAVE = True
except ImportError:  # the web image has no pandas: these tests run in the numeric environment
    HAVE = False


def frozen_run():
    assert hashlib.sha256((ROOT / "run.py").read_bytes()).hexdigest() == FROZEN_RUN_SHA, "frozen run.py changed"
    spec = importlib.util.spec_from_file_location("phase5_frozen_run", ROOT / "run.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(HAVE, "numpy/pandas not installed (numeric job environment only)")
class Phase5FrozenBoundaries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p5 = frozen_run()
        cls.cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

    def frame(self, close, open_=None):
        n = len(close)
        return pd.DataFrame({"local_date": pd.bdate_range("2001-01-01", periods=n).strftime("%Y-%m-%d"),
                             "open": np.asarray(open_ if open_ is not None else close, float), "close": np.asarray(close, float)})

    # ---------- P2 stress memory: 20 bars, then expired ----------
    def p2_case(self, rise_from):
        """380 closes: 100 before bar 300, 70 from bar 300 (the 30% drop makes bar 300.. stress days while
        r20 <= -8%). The last stress day is bar 319 (r20 at bar 320 compares with bar 300 = 70: 0%).
        From `rise_from` the close rises to 70.5, giving r5 > 0 and close > MA20."""
        c = np.full(380, 100.0); c[300:] = 70.0; c[rise_from:] = 70.5
        return self.p5.features(self.frame(c), self.cfg)

    def test_stress_days_end_at_bar_319(self):
        d = self.p2_case(338)
        stress = (d["dd252"] <= -0.15) & (d["r20"] <= -0.08)
        self.assertEqual(int(np.flatnonzero(stress.to_numpy()).max()), 319)
        self.assertEqual(int(np.flatnonzero(stress.to_numpy()).min()), 300)

    def test_memory_still_valid_when_confirmation_at_bar_338(self):
        d = self.p2_case(338)  # bar 338: last stress 319 is 19 bars back -> inside the 20-bar memory (bars 319..338)
        self.assertTrue(bool(d.at[338, "recent_stress"]))
        self.assertGreater(d.at[338, "r5"], 0); self.assertGreater(d.at[338, "close"], d.at[338, "ma20"])
        self.assertEqual(d.at[338, "P2_RECOVERY"], 1.0)
        self.assertEqual(d.at[337, "P2_RECOVERY"], 0.0)

    def test_memory_expired_when_confirmation_at_bar_339(self):
        d = self.p2_case(339)  # bar 339: window 320..339 holds no stress day -> expired, no entry
        self.assertFalse(bool(d.at[339, "recent_stress"]))
        self.assertGreater(d.at[339, "r5"], 0); self.assertGreater(d.at[339, "close"], d.at[339, "ma20"])
        self.assertEqual(float(d["P2_RECOVERY"].iloc[300:].max()), 0.0)

    # ---------- warm-up through the real simulator ----------
    def intervals(self, n):
        c = np.linspace(100, 120, n)
        part = self.p5.features(self.frame(c, c * 0.999), self.cfg); part["episode"] = 0
        return self.p5.simulate([part], "BUY_HOLD", 10, "1900-01-01", "2100-01-01", self.cfg), part

    def test_warmup_252_253_254_bars(self):
        counts = {}
        for n in (252, 253, 254):
            ledger, part = self.intervals(n)
            counts[n] = len(ledger)
            if len(ledger):
                first = ledger.iloc[0]
                idx = {k: int(np.flatnonzero(part["local_date"].to_numpy() == first[k])[0]) for k in ("signal_date", "exec_date", "mark_date")}
                self.assertEqual(idx, {"signal_date": 251, "exec_date": 252, "mark_date": 253})
                self.assertTrue(bool(first["terminal_liquidation"]))
        self.assertEqual(counts, {252: 0, 253: 0, 254: 1})


if __name__ == "__main__":
    unittest.main()
