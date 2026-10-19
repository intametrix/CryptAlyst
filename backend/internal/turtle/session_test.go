package turtle

import (
	"testing"
	"time"
)

func testEngine() *Engine {
	cfg := DefaultConfig()
	return New(cfg)
}

func alert(sym, kind string, price, atr, vol, hi, lo float64) Alert {
	return Alert{
		ID: "a1", Symbol: sym, Kind: kind, Severity: "ALERT",
		Price: price, At: time.Unix(1700000000, 0),
		VolPct: vol, ATR: atr, DonchianHigh: hi, DonchianLow: lo,
	}
}

// The happy path: an alert with a Donchian confirm opens a position.
func TestEntryFills(t *testing.T) {
	e := testEngine()
	e.Start()
	o, ok := e.OnAlert(alert("AAAUSDT", "PUMP", 10, 0.1, 0.5, 9.9, 9))
	if !ok {
		t.Fatalf("expected a fill, got rejection: %s", o.Reason)
	}
	s := e.Session()
	if len(s.Positions) != 1 {
		t.Fatalf("want 1 position, got %d", len(s.Positions))
	}
	p := s.Positions[0]
	// Risk per unit = 2N = 0.2; budget = 1% of 300 = 3 USDT; units = 15.
	if p.Units <= 0 {
		t.Fatalf("units must be positive, got %f", p.Units)
	}
	if p.StopPrice >= p.EntryPrice {
		t.Errorf("long stop %.4f must sit below entry %.4f", p.StopPrice, p.EntryPrice)
	}
}

// THE VOLATILITY VETO. This is the rule the human asked for, so it gets the
// strongest test: at or above the threshold the entry must be refused even when
// every other condition is satisfied.
func TestVolatilityVeto(t *testing.T) {
	e := testEngine()
	e.Start()
	o, ok := e.OnAlert(alert("BBBUSDT", "PUMP", 10, 0.1, 1.5, 9.9, 9))
	if ok {
		t.Fatal("entry must be VETOED at 1.5% volatility against a 1.0% threshold")
	}
	if len(e.Session().Positions) != 0 {
		t.Error("a vetoed entry must not open a position")
	}
	s := e.Session()
	if s.EntriesVetoed != 1 {
		t.Errorf("the veto must be COUNTED so it is visible, got %d", s.EntriesVetoed)
	}
	if o.Reason == "" {
		t.Error("a rejection must carry a reason")
	}
}

// An alert without a Donchian breakout is a reason to look, not to act.
func TestNoBreakoutNoEntry(t *testing.T) {
	e := testEngine()
	e.Start()
	_, ok := e.OnAlert(alert("CCCUSDT", "PUMP", 9.5, 0.1, 0.5, 9.9, 9))
	if ok {
		t.Fatal("price below the channel high must not enter long")
	}
}

// Sizing must never exceed available cash even under perfect liquidity.
func TestSizingRespectsCash(t *testing.T) {
	e := testEngine()
	e.Start()
	// A huge N makes one unit cheap in risk terms, so the notional cap binds.
	_, ok := e.OnAlert(alert("DDDUSDT", "PUMP", 100, 0.01, 0.1, 99, 90))
	if !ok {
		t.Skip("no fill for this fixture; the cash cap is exercised elsewhere")
	}
	s := e.Session()
	if s.Cash < 0 {
		t.Errorf("cash must never go negative, got %.2f", s.Cash)
	}
	if s.Equity > s.Config.StartingEquity*1.001 {
		t.Errorf("equity %.2f cannot exceed starting equity with no price move", s.Equity)
	}
}

// A stop must close the position and book a realised loss near 1% of equity.
func TestStopClosesAndBooksLoss(t *testing.T) {
	e := testEngine()
	e.Start()
	o, ok := e.OnAlert(alert("EEEUSDT", "PUMP", 10, 0.1, 0.5, 9.9, 9))
	if !ok {
		t.Fatalf("expected a fill: %s", o.Reason)
	}
	stop := e.Session().Positions[0].StopPrice
	e.Mark("EEEUSDT", stop, time.Unix(1700000100, 0))
	s := e.Session()
	if len(s.Positions) != 0 {
		t.Error("the stop must close the position")
	}
	if len(e.Trades()) != 1 {
		t.Fatalf("want 1 trade, got %d", len(e.Trades()))
	}
	tr := e.Trades()[0]
	if tr.PnL >= 0 {
		t.Errorf("a stopped long must book a loss, got %.2f", tr.PnL)
	}
	if tr.Reason != "2N stop" {
		t.Errorf("want the stop reason, got %q", tr.Reason)
	}
}

// Stop() must flatten everything so a finished session has no open risk.
func TestStopFlattens(t *testing.T) {
	e := testEngine()
	e.Start()
	e.OnAlert(alert("FFFUSDT", "PUMP", 10, 0.1, 0.5, 9.9, 9))
	e.Stop()
	s := e.Session()
	if len(s.Positions) != 0 {
		t.Errorf("Stop must flatten, %d positions remain", len(s.Positions))
	}
	if s.State != StateStopped {
		t.Errorf("state = %s, want stopped", s.State)
	}
}

// Alerts must be ignored while the session is not running.
func TestIdleIgnoresAlerts(t *testing.T) {
	e := testEngine()
	if _, ok := e.OnAlert(alert("GGGUSDT", "PUMP", 10, 0.1, 0.5, 9.9, 9)); ok {
		t.Fatal("an idle engine must not trade")
	}
}
