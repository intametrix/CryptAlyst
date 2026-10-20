package onset

import (
	"strings"
	"testing"

	"pumpdump/backend/internal/flow"
)

// TestBookMissingEarnsNoQualityPoints is the regression test for missing
// top-of-book data being scored as a perfect book. The wire carries
// SpreadPct=0 when the ticker had no usable bid/ask, and the old scorer read
// that as "zero spread, full marks". A candidate flagged BookMissing must
// earn zero spread and depth points -- absent data is unscored, not excellent.
func TestBookMissingEarnsNoQualityPoints(t *testing.T) {
	cfg := flow.DefaultConfig()

	base := flow.Candidate{
		Direction:      1,
		BurstMultiple:  cfg.MinBurstMultiple,
		BurstZ:         cfg.MinBurstZ,
		SigmaMultiple:  cfg.MinSigmaMultiple,
		QuoteVolume24h: 10 * cfg.MinAbsFlow, // full liquidity points in both cases
	}

	withBook := base
	withBook.SpreadPct = 0 // tight book: earns the full 6 spread points
	withBook.BookDepthUSD = 20 * cfg.MinBookDepthUSD
	withBook.BookMissing = false

	missing := base
	missing.BookMissing = true // SpreadPct 0 and BookDepthUSD 0, but because the data is ABSENT

	rWith := Score(withBook, cfg, 0)
	rMissing := Score(missing, cfg, 0)

	if rMissing.Components["quality"] >= rWith.Components["quality"] {
		t.Fatalf("missing book (quality %.2f) must score strictly below a real tight book (quality %.2f)",
			rMissing.Components["quality"], rWith.Components["quality"])
	}
	// Liquidity points are identical, so the gap is exactly the spread (6) plus
	// depth (4) points the missing candidate must not receive.
	if diff := rWith.Components["quality"] - rMissing.Components["quality"]; diff != 10 {
		t.Fatalf("quality gap = %.2f, want exactly 10 (6 spread + 4 depth points)", diff)
	}
	found := false
	for _, r := range rMissing.Reasons {
		if strings.Contains(r, "no book data") {
			found = true
		}
	}
	if !found {
		t.Fatalf("missing book must be explained in the reasons, got %v", rMissing.Reasons)
	}
}
