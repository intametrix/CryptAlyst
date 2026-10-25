package detect

import (
	"testing"

	"pumpdump/backend/internal/flow"
)

func TestBullBearScoreDirection(t *testing.T) {
	up := 0.004 // +0.4% in 10s
	down := -0.004
	bull := flow.Candidate{
		Symbol: "AAAUSDT", BurstMultiple: 8, TakerBuyRatio: 0.7,
		Change10s: &up, Price: 100,
	}
	bear := flow.Candidate{
		Symbol: "BBBUSDT", BurstMultiple: 8, TakerBuyRatio: 0.3,
		Change10s: &down, Price: 100,
	}

	bu, be, _ := bullBearScore(bull)
	if bu <= be {
		t.Fatalf("bull candidate: bull=%v should exceed bear=%v", bu, be)
	}
	bu2, be2, _ := bullBearScore(bear)
	if be2 <= bu2 {
		t.Fatalf("bear candidate: bear=%v should exceed bull=%v", be2, bu2)
	}
}

func TestBullBearScoreCalmIsLow(t *testing.T) {
	zero := 0.0
	c := flow.Candidate{Symbol: "CCCUSDT", BurstMultiple: 1, TakerBuyRatio: 0.5, Change10s: &zero}
	_, _, total := bullBearScore(c)
	if total > 30 {
		t.Fatalf("calm candidate total=%v, want < 30", total)
	}
}
