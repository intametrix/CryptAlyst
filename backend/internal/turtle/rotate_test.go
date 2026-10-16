package turtle

import (
	"testing"
	"time"
)

func sel(syms ...string) []Scored {
	out := make([]Scored, 0, len(syms))
	for i, s := range syms {
		out = append(out, Scored{
			Candidate: Candidate{Symbol: s, Price: 1},
			Score:     float64(len(syms)-i) / 10,
		})
	}
	return out
}

// THE RULE THAT MATTERS MOST: rotation must never drop a symbol we hold a
// position on. The selector ranks opportunity; it does not get to close risk.
// If it did, the next mark on that symbol would be ignored and the 2N stop would
// never fire -- a silent way to hold a loser forever.
func TestRotationKeepsHeldPositions(t *testing.T) {
	e := New(DefaultConfig())
	e.Start()
	e.SetWatchlist(sel("AAAUSDT", "BBBUSDT"))

	// Open a position on AAAUSDT.
	if _, ok := e.OnAlert(alert("AAAUSDT", "PUMP", 10, 0.1, 0.5, 9.9, 9)); !ok {
		t.Fatal("setup failed: expected a fill on AAAUSDT")
	}

	// Now rotate it AWAY, keeping only CCCUSDT.
	rec := e.RotateWatchlist(sel("CCCUSDT"), "test rotation")

	if _, still := e.watchlist["AAAUSDT"]; !still {
		t.Error("a held symbol must be RETAINED through rotation")
	}
	if len(rec.HeldRetained) != 1 || rec.HeldRetained[0] != "AAAUSDT" {
		t.Errorf("HeldRetained = %v, want [AAAUSDT] so the retention is visible", rec.HeldRetained)
	}
	if len(rec.Added) != 1 || rec.Added[0] != "CCCUSDT" {
		t.Errorf("Added = %v, want [CCCUSDT]", rec.Added)
	}
	if len(rec.Removed) != 1 || rec.Removed[0] != "BBBUSDT" {
		t.Errorf("Removed = %v, want [BBBUSDT]", rec.Removed)
	}
}

// A held position must still be stoppable AFTER a rotation that dropped it --
// which is the actual consequence the rule protects against.
func TestHeldPositionStillStopsAfterRotation(t *testing.T) {
	e := New(DefaultConfig())
	e.Start()
	e.SetWatchlist(sel("AAAUSDT"))
	if _, ok := e.OnAlert(alert("AAAUSDT", "PUMP", 10, 0.1, 0.5, 9.9, 9)); !ok {
		t.Fatal("setup failed")
	}
	stop := e.Session().Positions[0].StopPrice

	e.RotateWatchlist(sel("ZZZUSDT"), "rotate away from the held symbol")
	e.Mark("AAAUSDT", stop, time.Unix(1700000100, 0))

	if len(e.Session().Positions) != 0 {
		t.Error("the stop must still fire on a retained symbol after rotation")
	}
	if len(e.Trades()) != 1 {
		t.Errorf("want 1 closed trade, got %d", len(e.Trades()))
	}
}

// Rotation must be RECORDED, because a watchlist that changes silently makes
// every downstream result unattributable.
func TestRotationIsRecorded(t *testing.T) {
	e := New(DefaultConfig())
	e.Start()
	e.SetWatchlist(sel("AAAUSDT"))
	e.RotateWatchlist(sel("BBBUSDT"), "second")
	e.RotateWatchlist(sel("CCCUSDT"), "third")

	rs := e.Rotations()
	if len(rs) != 2 {
		t.Fatalf("want 2 rotations recorded, got %d", len(rs))
	}
	// Newest first.
	if rs[0].Reason != "third" {
		t.Errorf("newest rotation reason = %q, want third", rs[0].Reason)
	}
	if rs[0].Seq <= rs[1].Seq {
		t.Errorf("sequence must increase: %d then %d", rs[1].Seq, rs[0].Seq)
	}
	if e.RotationCount() != 2 {
		t.Errorf("RotationCount = %d, want 2", e.RotationCount())
	}
}

// Crypto defaults must actually be FASTER than the book's. If they are not, the
// speed change is cosmetic.
func TestCryptoDefaultsAreFaster(t *testing.T) {
	book := BookDefaults()
	crypto := CryptoDefaults()

	// THE SPEED IS IN THE BAR INTERVAL, NOT THE BAR COUNT.
	//
	// My first version of this test compared bar counts and failed, correctly: I
	// had set identical counts and still claimed the crypto defaults were faster.
	// The claim was untestable because the interval was not in the config. Now it
	// is, and the assertion is on WALL-CLOCK horizon, which is what "faster"
	// actually means.
	bookS2 := book.HorizonSeconds(System2)
	cryptoS2 := crypto.HorizonSeconds(System2)
	if cryptoS2 >= bookS2 {
		t.Errorf("crypto System 2 horizon %.0fs must be shorter than the book's %.0fs", cryptoS2, bookS2)
	}
	// The book's default with no interval set is daily, so this should be a large
	// multiple, not a marginal one.
	if ratio := bookS2 / cryptoS2; ratio < 50 {
		t.Errorf("crypto horizon is only %.0fx shorter than the book's; this is meant to be a tempo change, not a tweak", ratio)
	}
	// And the same bar COUNT, which is the structural point: the rules are
	// unchanged, only the clock they run on.
	if crypto.S1Entry != book.S1Entry || crypto.S2Entry != book.S2Entry {
		t.Errorf("crypto must keep the book's bar counts (%d/%d), got %d/%d",
			book.S1Entry, book.S2Entry, crypto.S1Entry, crypto.S2Entry)
	}
	// The STRUCTURAL rules must be unchanged: making them looser would not be a
	// faster Turtle, it would be a different system wearing the name.
	if crypto.RiskPct != book.RiskPct {
		t.Errorf("risk per unit must stay at 1%%, got %v", crypto.RiskPct)
	}
	if crypto.StopN != book.StopN {
		t.Errorf("the stop must stay at 2N, got %v", crypto.StopN)
	}
	if crypto.AddStepN != book.AddStepN {
		t.Errorf("pyramiding must stay at 0.5N, got %v", crypto.AddStepN)
	}
	if crypto.MaxUnitsPerMarket != book.MaxUnitsPerMarket {
		t.Errorf("the per-market unit cap must stay at %d", book.MaxUnitsPerMarket)
	}
	if crypto.MaxUnitsCorrelated != book.MaxUnitsCorrelated {
		t.Errorf("the correlated cap must stay at %d", book.MaxUnitsCorrelated)
	}
}
