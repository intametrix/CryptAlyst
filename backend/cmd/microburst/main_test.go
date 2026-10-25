package main

import "testing"

func TestBarrierSeconds(t *testing.T) {
	cases := map[string]int{
		"label_barrier_10s": 10,
		"label_barrier_30s": 30,
		"label_barrier_60s": 60,
		"label_10s":         0, // not a barrier label
		"":                  0,
	}
	for in, want := range cases {
		if got := barrierSeconds(in); got != want {
			t.Fatalf("barrierSeconds(%q)=%d want %d", in, got, want)
		}
	}
}

func TestJoinMapsLabels(t *testing.T) {
	preds := map[string]pred{
		"a": {ID: "a", Symbol: "AAAUSDT", TSMS: 100, Features: map[string]float64{"burst_multiple": 8}},
		"b": {ID: "b", Symbol: "BBBUSDT", TSMS: 200, Features: map[string]float64{"burst_multiple": 10}},
		"c": {ID: "c", Symbol: "CCCUSDT", TSMS: 300, Features: map[string]float64{"burst_multiple": 6}},
	}
	labels := map[int][]record{
		10: {
			{ID: "a", Label: "UP"},
			{ID: "b", Label: "REVERSAL"},
			// c has no label -> NEITHER
		},
	}
	rows := join(preds, labels, 10)
	if len(rows) != 3 {
		t.Fatalf("join len=%d want 3", len(rows))
	}
	bySym := map[string]row{}
	for _, r := range rows {
		bySym[r.symbol] = r
	}
	if bySym["AAAUSDT"].up != 1 || bySym["AAAUSDT"].rev != 0 {
		t.Fatalf("AAAUSDT should be UP: %+v", bySym["AAAUSDT"])
	}
	if bySym["BBBUSDT"].up != 0 || bySym["BBBUSDT"].rev != 1 {
		t.Fatalf("BBBUSDT should be REVERSAL: %+v", bySym["BBBUSDT"])
	}
	if bySym["CCCUSDT"].up != 0 || bySym["CCCUSDT"].rev != 0 {
		t.Fatalf("CCCUSDT should be NEITHER: %+v", bySym["CCCUSDT"])
	}
}

func TestSplitChronological(t *testing.T) {
	rows := make([]row, 10)
	for i := range rows {
		rows[i] = row{tsms: int64(i * 1000)}
	}
	train, holdout := split(rows, 0.7)
	if len(train) != 7 || len(holdout) != 3 {
		t.Fatalf("split train=%d holdout=%d want 7/3", len(train), len(holdout))
	}
	// Chronological: train's max ts < holdout's min ts.
	if train[len(train)-1].tsms >= holdout[0].tsms {
		t.Fatalf("split is not chronological: train ends at %d, holdout starts at %d",
			train[len(train)-1].tsms, holdout[0].tsms)
	}
}
