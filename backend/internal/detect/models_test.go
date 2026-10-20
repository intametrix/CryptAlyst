package detect

import "testing"

// TestAcceptedSurveySetupsSkipsRejected is the regression test for the Turtle
// breakout detector republishing every survey setup as an alert, including
// the ones the survey had explicitly rejected. A rejection (volume gate, ATR
// band, BTC regime, budget) is explanation, not detection, and must never
// reach the feed, the persisted history or the prediction ledger.
func TestAcceptedSurveySetupsSkipsRejected(t *testing.T) {
	payload := map[string]any{
		"type": "survey",
		"setups": []map[string]any{
			{"symbol": "BTCUSDT", "timeframe": "4h", "accepted": true, "score": 80.0, "direction": 1},
			{"symbol": "ETHUSDT", "timeframe": "4h", "accepted": false, "reject_reason": "volume gate"},
			// No "accepted" key at all: a malformed or pre-gate entry must not
			// emit either -- absence of acceptance is not acceptance.
			{"symbol": "SOLUSDT", "timeframe": "4h"},
			{"symbol": "XRPUSDT", "timeframe": "1d", "accepted": true, "score": 40.0, "direction": -1},
		},
	}

	got := acceptedSurveySetups(payload)
	if len(got) != 2 {
		t.Fatalf("accepted setups = %d, want 2 (rejected and keyless setups must be skipped): %v", len(got), got)
	}
	if got[0]["symbol"] != "BTCUSDT" || got[1]["symbol"] != "XRPUSDT" {
		t.Fatalf("wrong setups survived the filter: %v, %v", got[0]["symbol"], got[1]["symbol"])
	}
}

// TestAcceptedSurveySetupsToleratesBadPayload: a missing or wrongly-typed
// setups field is "nothing to publish", not a panic.
func TestAcceptedSurveySetupsToleratesBadPayload(t *testing.T) {
	if got := acceptedSurveySetups(nil); got != nil {
		t.Fatalf("nil payload should yield nil setups, got %v", got)
	}
	if got := acceptedSurveySetups(map[string]any{}); got != nil {
		t.Fatalf("payload without setups should yield nil setups, got %v", got)
	}
	// []any is the JSON-round-tripped shape; the detector only accepts the
	// concrete []map[string]any the survey builds in-process.
	if got := acceptedSurveySetups(map[string]any{"setups": []any{map[string]any{"accepted": true}}}); got != nil {
		t.Fatalf("wrongly-typed setups should yield nil setups, got %v", got)
	}
}
