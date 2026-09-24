import io
import json
import math
import unittest
from unittest import mock

from tornmarket import indicators as ind
from tornmarket.api import Holding, Lot, TornAPIError, TornClient, parse_holdings, parse_stocks
from tornmarket.backtest import run_backtest
from tornmarket.bot import Bot
from tornmarket.cli import main
from tornmarket.config import Config, StrategyConfig
from tornmarket.notify import Notifier
from tornmarket.storage import Store
from tornmarket.strategy import BUY, HOLD, SELL, evaluate, min_bars

MARKET = {
    "stocks": {
        "1": {
            "stock_id": 1, "name": "Torn & Shanghai Banking", "acronym": "TSB",
            "current_price": 1054.53, "market_cap": 1e12, "total_shares": 1000000000,
            "investors": 5000,
            "benefit": {"type": "active", "frequency": 31, "requirement": 3000000,
                        "description": "$50,000,000"},
        },
        "2": {"stock_id": 2, "name": "Torn City Investments", "acronym": "TCI",
              "current_price": 900.0, "benefit": {}},
    }
}
PORTFOLIO = {
    "stocks": {
        "1": {"stock_id": 1, "total_shares": 3000,
              "transactions": {"11": {"shares": 1000, "bought_price": 1000.0, "time_bought": 100},
                               "12": {"shares": 2000, "bought_price": 1100.0, "time_bought": 200}}}
    }
}


def wave(n, start=100.0, drift=0.0, amp=0.0, period=40):
    return [start * (1 + drift) ** i + amp * math.sin(2 * math.pi * i / period) for i in range(n)]


class IndicatorTests(unittest.TestCase):
    def test_sma_and_ema(self):
        self.assertEqual(ind.sma([1, 2, 3, 4, 5], 3), [None, None, 2, 3, 4])
        e = ind.ema([1, 2, 3, 4, 5], 3)
        self.assertEqual(e[:2], [None, None])
        self.assertAlmostEqual(e[2], 2.0)
        self.assertAlmostEqual(e[3], 3.0)
        self.assertAlmostEqual(e[4], 4.0)

    def test_rsi_extremes(self):
        self.assertEqual(ind.rsi(list(range(1, 30)), 14)[-1], 100.0)
        self.assertEqual(ind.rsi(list(range(30, 1, -1)), 14)[-1], 0.0)
        self.assertEqual(ind.rsi([5.0] * 20, 14)[-1], 50.0)

    def test_rsi_known_value(self):
        # Classic Wilder example series; first RSI(14) value is ~70.46.
        closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
                  45.89, 46.03, 45.61, 46.28, 46.28]
        self.assertAlmostEqual(ind.rsi(closes, 14)[14], 70.46, places=1)

    def test_macd_and_bollinger_shapes(self):
        data = wave(80, amp=5)
        line, sig, hist = ind.macd(data)
        self.assertIsNone(line[24])
        self.assertIsNotNone(line[25])
        self.assertIsNone(sig[32])
        self.assertIsNotNone(sig[33])
        self.assertAlmostEqual(hist[-1], line[-1] - sig[-1])
        lower, mid, upper = ind.bollinger(data)
        self.assertTrue(lower[-1] < mid[-1] < upper[-1])
        flat_lower, _, flat_upper = ind.bollinger([10.0] * 25)
        self.assertEqual(flat_lower[-1], flat_upper[-1])

    def test_resample_keeps_last_price_per_bar(self):
        pts = [(0, 1.0), (30, 2.0), (60, 3.0), (119, 4.0), (120, 5.0)]
        self.assertEqual(ind.resample(pts, 60), [(0, 2.0), (60, 4.0), (120, 5.0)])


class ApiTests(unittest.TestCase):
    def test_parse_stocks(self):
        quotes = parse_stocks(MARKET)
        self.assertEqual([q.acronym for q in quotes], ["TSB", "TCI"])
        self.assertEqual(quotes[0].benefit.requirement, 3000000)
        self.assertEqual(quotes[1].benefit.requirement, 0)

    def test_parse_holdings(self):
        h = parse_holdings(PORTFOLIO)[1]
        self.assertEqual(h.total_shares, 3000)
        self.assertAlmostEqual(h.avg_cost, (1000 * 1000 + 2000 * 1100) / 3000)
        self.assertEqual(h.first_bought, 100)

    def _client_with(self, *payloads):
        responses = [io.BytesIO(json.dumps(p).encode()) for p in payloads]
        client = TornClient("k", max_retries=2)
        client.limiter.acquire = lambda: None
        patcher = mock.patch("urllib.request.urlopen", side_effect=responses)
        self.addCleanup(patcher.stop)
        return client, patcher.start()

    def test_client_retries_rate_limit_then_succeeds(self):
        client, urlopen = self._client_with({"error": {"code": 5, "error": "Too many requests"}}, MARKET)
        with mock.patch("time.sleep"):
            quotes = client.market()
        self.assertEqual(len(quotes), 2)
        self.assertEqual(urlopen.call_count, 2)
        self.assertIn("selections=stocks", urlopen.call_args[0][0])

    def test_client_raises_on_bad_key(self):
        client, _ = self._client_with({"error": {"code": 2, "error": "Incorrect key"}})
        with self.assertRaises(TornAPIError) as ctx:
            client.market()
        self.assertEqual(ctx.exception.code, 2)


class StrategyTests(unittest.TestCase):
    def setUp(self):
        self.cfg = StrategyConfig()

    def test_warmup_holds(self):
        sig = evaluate([100.0] * 10, self.cfg)
        self.assertEqual(sig.action, HOLD)
        self.assertIn("warming up", sig.reasons[0])

    def test_uptrend_after_dip_buys(self):
        # Long steady decline then a sharp recovery: golden cross / oversold bounce territory.
        closes = [100 - 0.5 * i for i in range(60)] + [70 + 1.5 * i for i in range(15)]
        actions = [evaluate(closes[: i + 1], self.cfg).action for i in range(min_bars(self.cfg), len(closes))]
        self.assertIn(BUY, actions)

    def test_crash_sells(self):
        closes = [100 + 0.5 * i for i in range(60)] + [130 - 2 * i for i in range(15)]
        actions = [evaluate(closes[: i + 1], self.cfg).action for i in range(min_bars(self.cfg), len(closes))]
        self.assertIn(SELL, actions)

    def test_stop_loss_overrides(self):
        closes = [100.0] * 70 + [90.0]
        held = Holding(1, 100, [Lot(100, 100.0, 0)])
        sig = evaluate(closes, self.cfg, held, peak_since_buy=100.0)
        self.assertEqual(sig.action, SELL)
        self.assertIn("stop-loss", sig.reasons[0])

    def test_take_profit_and_benefit_warning(self):
        closes = [100.0] * 70 + [120.0]
        held = Holding(1, 5000, [Lot(5000, 100.0, 0)])
        sig = evaluate(closes, self.cfg, held, peak_since_buy=120.0, benefit_requirement=3000)
        self.assertEqual(sig.action, SELL)
        self.assertIn("take-profit", sig.reasons[0])
        self.assertIn("sell at most 2,000", sig.warnings[0])

    def test_trailing_stop(self):
        closes = [100.0] * 70 + [111.0]
        held = Holding(1, 10, [Lot(10, 100.0, 0)])
        sig = evaluate(closes, self.cfg, held, peak_since_buy=120.0)
        self.assertEqual(sig.action, SELL)
        self.assertIn("trailing stop", sig.reasons[0])


class BacktestTests(unittest.TestCase):
    def test_backtest_runs_and_accounts_fees(self):
        closes = wave(400, amp=15, period=60)
        bars = [(i * 900, p) for i, p in enumerate(closes)]
        res = run_backtest(bars, StrategyConfig())
        self.assertGreater(len(res.trades), 0)
        self.assertGreaterEqual(res.max_drawdown_pct, 0)
        # Strategy return compounds the closed trades plus any position still open at the end.
        compounded = 1.0
        for t in res.trades:
            compounded *= 1 + t.return_pct(0.1) / 100
        open_ret = (res.return_pct + 100) / (compounded * 100)
        self.assertGreater(open_ret, 0)
        if abs(open_ret - 1) > 1e-9:  # a position was still open: it must be a long from a later entry
            self.assertGreater(bars[-1][0], res.trades[-1].exit_ts)

    def test_backtest_too_short(self):
        self.assertEqual(run_backtest([(0, 1.0)] * 5, StrategyConfig()).trades, [])


class BotTests(unittest.TestCase):
    def test_tick_stores_and_announces_only_changes(self):
        cfg = Config(db_path=":memory:", strategy=StrategyConfig(bar_seconds=60))
        store = Store(":memory:")
        # Pre-load a rally then a crash in TCI; the tick's price completes the death cross.
        closes = [100 + 0.5 * i for i in range(60)] + [130 - 2 * i for i in range(7)]
        store.add_prices([(2, i * 60, p) for i, p in enumerate(closes)])
        client = mock.Mock()
        market = json.loads(json.dumps(MARKET))
        market["stocks"]["2"]["current_price"] = 116.0
        client.market.return_value = parse_stocks(market)
        client.portfolio.return_value = {}
        sent = []
        notifier = Notifier()
        notifier.send = sent.append
        bot = Bot(cfg, client, store, notifier)

        first = bot.tick(now=len(closes) * 60)
        self.assertEqual([(m["acronym"], s.action) for m, s in first], [("TCI", SELL)])
        self.assertEqual(len(sent), 1)
        second = bot.tick(now=len(closes) * 60 + 30)
        self.assertEqual(second, [])
        self.assertEqual(store.last_signal(2), SELL)


class CliTests(unittest.TestCase):
    def test_import_and_backtest(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            csv_path, db = os.path.join(d, "h.csv"), os.path.join(d, "t.db")
            with open(csv_path, "w") as fh:
                fh.write("stock_id,timestamp,price\n")
                for i, p in enumerate(wave(300, amp=10)):
                    fh.write(f"5,{i * 900},{p}\n")
            with open(os.path.join(d, "c.toml"), "w") as fh:
                fh.write(f'db_path = "{db}"\n[strategy]\nbar_seconds = 900\n')
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                self.assertEqual(main(["-c", os.path.join(d, "c.toml"), "import", csv_path]), 0)
                self.assertEqual(main(["-c", os.path.join(d, "c.toml"), "backtest"]), 0)
                self.assertEqual(main(["-c", os.path.join(d, "c.toml"), "signals", "--all"]), 0)
            text = out.getvalue()
            self.assertIn("Imported 300", text)
            self.assertIn("buy&hold", text)
            self.assertIn("score", text)


if __name__ == "__main__":
    unittest.main()
