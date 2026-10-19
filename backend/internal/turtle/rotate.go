package turtle

import "time"

// Watchlist rotation, and why the classic Turtle speeds are wrong for crypto.
//
// THE SPEED PROBLEM. The book was written for daily bars on futures. A 20-day
// entry channel and a 55-day confirmation are slow by construction, and that was
// appropriate: those markets moved on harvests, central-bank meetings and
// shipping schedules, and a signal that took three weeks to form was a signal
// about something real.
//
// Crypto does not work that way. A symbol can be discovered, pumped, distributed
// and abandoned inside a day. Running 20-day channels on it means the entry fires
// after the move is over, and the 2N stop is then taken out by the retrace. The
// rules stay the rules -- N, 2N stops, 0.5N pyramiding, unit limits by correlation
// are all sound at any timescale -- but the LOOKBACKS have to be expressed in the
// horizon the market actually trades on.
//
// So the defaults below are shorter by roughly two orders of magnitude, and every
// one of them is a named constant with the reasoning attached so a reader can see
// it was chosen rather than inherited.

// CryptoDefaults returns a rule set sized for crypto's tempo.
//
// The structural rules are UNCHANGED from the book: 1% risk per unit, a 2N stop,
// pyramiding at 0.5N, four units per market, six per correlated group. Only the
// lookbacks and the cadence move. Changing the risk rules to go faster would not
// be making the system faster, it would be making it a different system with the
// Turtle name on it.
func CryptoDefaults() BookConfig {
	c := BookDefaults()
	// N over 20 bars rather than 20 days. On 5-minute bars that is about 100
	// minutes of volatility, which is the horizon a crypto move actually lasts.
	c.NLookback = 20
	// System 1: 20-bar entry, 10-bar exit -- on the same 5-minute base, roughly
	// 100 and 50 minutes.
	c.S1Entry = 20
	c.S1Exit = 10
	// System 2 is the slower confirmation: about 4.5 hours of 5-minute bars.
	// Kept as a genuine second system rather than a slightly different first one.
	c.S2Entry = 55
	c.S2Exit = 20
	// Correlations are measured over a shorter window because crypto correlation
	// regimes change within hours, not weeks. A 120-bar lookback on 5-minute bars
	// is ten hours, which is the right memory for "are these the same trade".
	c.CorrelationLookback = 120
	// THE ACTUAL SPEED CHANGE, and it is this line rather than any bar count: a
	// five-minute bar against the book's daily bar is a 288x shorter horizon for
	// the SAME 20-bar and 55-bar channels. The book's System 2 looks 55 days
	// ahead; this looks 4.6 hours.
	c.BarIntervalSeconds = 300
	return c
}

// RotationInterval is how often a running session re-selects its universe.
//
// The human's instruction: "rotate its watchlist constantly based on indicators".
// At session start a watchlist is a snapshot of a market that has already moved on
// by the time the first alert arrives. Sixty seconds is short enough that a symbol
// becoming interesting is picked up while it is still interesting, and long enough
// that the selection is not chasing its own noise.
const RotationInterval = 30 * time.Second

// RotationRecord is what one rotation did, kept so the decision is auditable.
//
// Recorded rather than discarded because a watchlist that changes silently makes
// every downstream result unattributable: a trade that went well on a symbol added
// by rotation two minutes earlier is a different fact from one on a symbol chosen
// at session start, and only the log can tell them apart.
type RotationRecord struct {
	At       time.Time `json:"at"`
	Seq      int       `json:"seq"`
	Previous int       `json:"previous_size"`
	Current  int       `json:"current_size"`
	Added    []string  `json:"added"`
	Removed  []string  `json:"removed"`
	Kept     []string  `json:"kept"`
	// HeldRetained names symbols kept because a position is open on them, even if
	// the selector dropped them. Closing a position because a 60-second ranking
	// changed would be the selector overruling the risk rules.
	HeldRetained []string `json:"held_retained"`
	Reason       string   `json:"reason"`
}

// RotateWatchlist replaces the selected universe mid-session.
//
// TWO RULES THAT MATTER MORE THAN THE RANKING:
//
//  1. A SYMBOL WITH AN OPEN POSITION IS ALWAYS KEPT. The selector ranks
//     opportunity; it does not get to close risk. Dropping a held symbol would
//     mean the next mark on it is ignored and the stop never fires, which is a
//     silent way to hold a losing position forever.
//  2. ROTATION IS RECORDED, ALWAYS. Added, removed and retained are all logged
//     with the reason, so a result can be attributed to the watchlist that
//     produced it.
func (e *Engine) RotateWatchlist(sel []Scored, reason string) RotationRecord {
	e.mu.Lock()
	defer e.mu.Unlock()

	next := make(map[string]Scored, len(sel))
	for _, s := range sel {
		next[s.Symbol] = s
	}

	rec := RotationRecord{At: e.now(), Reason: reason}
	for sym := range next {
		if _, had := e.watchlist[sym]; !had {
			rec.Added = append(rec.Added, sym)
		}
	}

	// Rule 1: never drop a symbol we hold. Applied BEFORE the removed list is
	// built, because a retained symbol is not removed -- recording it as both was
	// a contradiction in the audit trail, and an audit trail that contradicts
	// itself cannot attribute a result to anything.
	for sym := range e.positions {
		if _, present := next[sym]; !present {
			next[sym] = Scored{
				Candidate: Candidate{Symbol: sym},
				Reasons:   []string{"retained: position open, the selector does not close risk"},
			}
			rec.HeldRetained = append(rec.HeldRetained, sym)
		}
	}

	for sym := range e.watchlist {
		if _, still := next[sym]; !still {
			rec.Removed = append(rec.Removed, sym)
		} else if _, wasHeld := e.positions[sym]; !wasHeld {
			rec.Kept = append(rec.Kept, sym)
		}
	}

	rec.Seq = e.rotationSeq + 1
	e.rotationSeq = rec.Seq
	rec.Previous = len(e.watchlist)
	e.watchlist = next

	ordered := make([]Scored, 0, len(next))
	for _, s := range sel {
		if _, ok := next[s.Symbol]; ok {
			ordered = append(ordered, s)
		}
	}
	// Fallback and retained entries keep their place at the end, so the ranking
	// that was actually chosen stays readable in order.
	for _, s := range next {
		if s.Score == 0 {
			ordered = append(ordered, s)
		}
	}
	e.selected = ordered
	e.selectedAt = e.now()
	e.rotations = append(e.rotations, rec)
	if len(e.rotations) > 200 {
		e.rotations = e.rotations[len(e.rotations)-100:]
	}
	rec.Current = len(e.watchlist)

	e.store.Record("watchlist_rotation", rec)
	return rec
}

// Rotations returns the rotation history, newest first.
func (e *Engine) Rotations() []RotationRecord {
	e.mu.Lock()
	defer e.mu.Unlock()
	out := make([]RotationRecord, len(e.rotations))
	copy(out, e.rotations)
	for i, j := 0, len(out)-1; i < j; i, j = i+1, j-1 {
		out[i], out[j] = out[j], out[i]
	}
	return out
}

// RotationsSince reports how many rotations have happened, for the health surface.
func (e *Engine) RotationCount() int {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.rotationSeq
}
