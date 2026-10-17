"""Smoke tests for the Turtle stack.  Stdlib unittest, no network required.

    python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from binance_turtle import indicators as ta
from binance_turtle.config import Config
from binance_turtle.strategy import BTCRegime, Position, size_unit, evaluate_entry
from binance_turtle.strategy import SymbolState


def bar(o, h, l, c, qv=1_000_000.0, t=0):
    return {"open_time": t, "open": o, "high": h, "low": l, "close": c,
            "volume": qv / max(c, 1e-9), "close_time": t, "quote_volume": qv,
            "trades": 10, "taker_buy_base": 0.0, "taker_buy_quote": 0.0}


class TestIndicators(unittest.TestCase):
    def test_atr_wilder_first_value_is_mean_of_tr(self):
        highs = [10, 11, 12, 13, 14, 15]
        lows = [8, 9, 10, 11, 12, 13]
        closes = [9, 10, 11, 12, 13, 14]
        atr = ta.atr_wilder(highs, lows, closes, period=3)
        self.assertIsNone(atr[1])
        self.assertIsNotNone(atr[2])
        tr = ta.true_range(highs, lows, closes)
        self.assertAlmostEqual(atr[2], sum(tr[:3]) / 3.0)
        # Wilder smoothing on the next bar
        self.assertAlmostEqual(atr[3], (atr[2] * 2 + tr[3]) / 3.0)

    def test_donchian_excludes_current_bar(self):
        highs = [1, 2, 3, 100, 4]
        lows = [0, 0, 0, 0, 0]
        up, dn = ta.donchian_prior(highs, lows, 3)
        # index 4 looks at highs[1:4] -> max 100, NOT the current bar
        self.assertEqual(up[4], 100)
        self.assertEqual(up[3], 3)

    def test_adx_direction_on_uptrend(self):
        closes = [100 + i for i in range(60)]
        highs = [c + 1 for c in closes]
        lows = [c - 1 for c in closes]
        adx, pdi, mdi = ta.adx_dmi(highs, lows, closes, period=14)
        self.assertIsNone(adx[2 * 14 - 2])
        self.assertIsNotNone(adx[2 * 14 - 1])
        self.assertGreater(pdi[-1], mdi[-1])
        self.assertGreater(adx[-1], 50)

    def test_volume_confirmation_fails_closed(self):
        cfg = Config()
        vols = [10_000.0] * 20 + [1_000.0]
        ok, med = ta.volume_confirmed(vols, 20, cfg)
        self.assertFalse(ok)                     # dust bar can never confirm
        self.assertEqual(med, 10_000.0)

        vols = [10_000.0] * 20 + [100_000.0]
        ok, _ = ta.volume_confirmed(vols, 20, cfg)
        self.assertFalse(ok)                     # 1.5*max(10k,20k)=30k ok, but <150k absolute

        vols = [10_000.0] * 20 + [200_000.0]
        ok, _ = ta.volume_confirmed(vols, 20, cfg)
        self.assertTrue(ok)

        vols = [1_000_000.0] * 20 + [1_400_000.0]
        ok, _ = ta.volume_confirmed(vols, 20, cfg)
        self.assertFalse(ok)                     # 1.4x < 1.5x median

    def test_percentile_rank(self):
        vals = [1, 2, 3, 4, 5, 6]
        pr = ta.percentile_rank(vals, 5)
        self.assertEqual(pr[-1], 1.0)
        self.assertIsNone(pr[4])


class TestSizing(unittest.TestCase):
    def test_wilder_formula(self):
        cfg = Config()
        qty, notes = size_unit(10_000, atr=4.80, price=220.0, lot_step=0.01,
                               median_hourly_qv=None, cfg=cfg)
        # 10000*0.005/4.80 = 10.4166 -> floor to 0.01 step
        self.assertAlmostEqual(qty, 10.41, places=2)
        self.assertNotIn("alt_stop_sanity", notes)

    def test_alt_stop_sanity_halves(self):
        cfg = Config()
        # 2N/price = 2*20/100 = 40% > 30% -> halve
        qty, notes = size_unit(10_000, atr=20.0, price=100.0, lot_step=0.001,
                               median_hourly_qv=None, cfg=cfg)
        self.assertIn("alt_stop_sanity", notes)
        self.assertAlmostEqual(qty, 10000 * 0.005 / 20.0 * 0.5, places=3)

    def test_participation_cap(self):
        cfg = Config()
        qty, notes = size_unit(10_000_000, atr=4.0, price=200.0, lot_step=0.001,
                               median_hourly_qv=100_000.0, cfg=cfg)
        # cap notional = 5% * 100k * (20/60) = 1666.67
        self.assertIn("participation_cap", notes)
        self.assertLessEqual(qty * 200.0, 1666.67 + 1e-6)


class TestPositionRatchet(unittest.TestCase):
    def test_stop_never_retreats_and_channel_takes_over(self):
        cfg = Config()
        p = Position("X", "trigger", 0, entry_price=100.0, atr=5.0, qty=1.0, cfg=cfg)
        self.assertAlmostEqual(p.stop, 100.0 - 2 * 5.0)      # 90
        p.update_ratchet(cfg, channel_low=80.0, price=101.0)
        self.assertAlmostEqual(p.stop, 90.0)                 # channel below 2N stop
        p.update_ratchet(cfg, channel_low=96.0, price=112.0)
        self.assertAlmostEqual(p.stop, 96.0)                 # channel governs
        p.update_ratchet(cfg, channel_low=70.0, price=112.0)
        self.assertAlmostEqual(p.stop, 96.0)                 # never retreats
        # "highest fill - 2N" only moves once a *fill* is higher, not the price
        p.update_ratchet(cfg, channel_low=70.0, price=130.0)
        self.assertAlmostEqual(p.stop, 96.0)
        p.add_unit(cfg, price=115.0, qty=1.0)                # highest fill -> 115
        p.update_ratchet(cfg, channel_low=70.0, price=118.0)
        self.assertAlmostEqual(p.stop, 105.0)                # 115 - 2N

    def test_time_stop(self):
        cfg = Config()
        p = Position("X", "primary", 0, 100.0, 5.0, 1.0, cfg)
        p.bars_held = 9
        self.assertFalse(p.time_stop_hit(cfg, 100.5))
        p.bars_held = 10
        self.assertTrue(p.time_stop_hit(cfg, 100.5))
        self.assertFalse(p.time_stop_hit(cfg, 120.0))

    def test_pyramid_caps(self):
        cfg = Config()
        p = Position("X", "trigger", 0, 100.0, 5.0, 1.0, cfg)
        self.assertFalse(p.can_pyramid(cfg, 102.0))          # < +0.5N
        self.assertTrue(p.can_pyramid(cfg, 102.6))
        p.add_unit(cfg, 102.6, 1.0)
        self.assertEqual(p.units, 2)
        self.assertFalse(p.can_pyramid(cfg, 120.0))          # 4h trade caps at 2


class TestBTCRegime(unittest.TestCase):
    def test_latch_hysteresis(self):
        cfg = Config()
        # 60 bars flat at 100, then a breakdown close below the 55-bar low
        bars = [bar(100, 101, 99, 100, t=i * 86_400_000) for i in range(60)]
        r = BTCRegime(cfg)
        self.assertFalse(r.update(bars))
        bars.append(bar(98, 99, 90, 92, t=60 * 86_400_000))
        self.assertTrue(r.update(bars))                      # broke the 55d low
        # a small bounce does NOT clear the latch
        bars.append(bar(93, 96, 92, 95, t=61 * 86_400_000))
        self.assertTrue(r.update(bars))
        # only a close above the prior 55-day high clears it
        bars.append(bar(96, 130, 96, 128, t=62 * 86_400_000))
        self.assertFalse(r.update(bars))


class TestEntryGates(unittest.TestCase):
    def _uptrend_state(self, breakout=True):
        cfg = Config()
        daily = []
        price = 100.0
        for i in range(300):
            price *= 1.002
            daily.append(bar(price * 0.995, price * 1.01, price * 0.99,
                             price, qv=50_000_000.0, t=i * 86_400_000))
        if breakout:
            last = daily[-1]
            last["close"] = last["high"] * 1.05   # close above the prior 55d high
            last["quote_volume"] = 500_000_000.0
        st = SymbolState("TESTUSDT", daily, None, cfg)
        return cfg, st

    def test_breakout_with_volume_and_band_scores(self):
        cfg, st = self._uptrend_state(breakout=True)
        setup, rejects = evaluate_entry(st, "primary", cfg)
        self.assertEqual(rejects, [])
        self.assertIsNotNone(setup)
        self.assertGreater(setup.score, 0.0)

    def test_no_breakout_is_rejected(self):
        cfg, st = self._uptrend_state(breakout=False)
        setup, rejects = evaluate_entry(st, "primary", cfg)
        self.assertIsNone(setup)
        self.assertTrue(any(r.startswith("no_breakout") for r in rejects))

    def test_btc_risk_off_blocks_entry(self):
        cfg, st = self._uptrend_state(breakout=True)
        setup, rejects = evaluate_entry(st, "primary", cfg, btc_risk_off=True)
        self.assertIsNone(setup)
        self.assertIn("btc_risk_off", rejects)

    def test_adx_is_not_a_veto(self):
        """answers s1: a breakout must never be rejected for weak ADX."""
        cfg, st = self._uptrend_state(breakout=True)
        cfg.adx_min = 99.0            # absurdly high; must not matter
        setup, rejects = evaluate_entry(st, "primary", cfg)
        self.assertIsNotNone(setup)
        self.assertFalse(any("adx" in r for r in rejects))

    def test_blowoff_filter(self):
        cfg, st = self._uptrend_state(breakout=True)
        # force RSI high and price far above the 200d mean
        st.rsi[-1] = 95.0
        st.sma_long[-1] = st.d_close[-1] / 2.0
        setup, rejects = evaluate_entry(st, "primary", cfg)
        self.assertIsNone(setup)
        self.assertTrue(any(r.startswith("blowoff") for r in rejects))


if __name__ == "__main__":
    unittest.main(verbosity=2)
