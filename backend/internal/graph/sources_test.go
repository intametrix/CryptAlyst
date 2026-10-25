package graph

import "testing"

func TestPriceSource(t *testing.T) {
	n := PriceSource("BTCUSDT", func(string) float64 { return 42000 })
	out, err := n.Eval(nil)
	if err != nil {
		t.Fatalf("Eval: %v", err)
	}
	if out["price"].Value != 42000 || out["price"].Symbol != "BTCUSDT" || out["price"].Type != TypePrice {
		t.Errorf("price signal = %+v", out["price"])
	}
}

func TestVolumeSource(t *testing.T) {
	n := VolumeSource("ETHUSDT", func(string) float64 { return 1.2e6 })
	out, _ := n.Eval(nil)
	if out["volume"].Value != 1.2e6 || out["volume"].Type != TypeVolume {
		t.Errorf("volume signal = %+v", out["volume"])
	}
}

func TestFlowSourceScalesToScore(t *testing.T) {
	n := FlowSource("BTCUSDT", func(string) float64 { return 0.71 })
	out, _ := n.Eval(nil)
	if out["flow"].Value != 71 { // 0.71 * 100
		t.Errorf("flow score = %v, want 71", out["flow"].Value)
	}
}

func TestRegimeSource(t *testing.T) {
	cases := []struct {
		regime string
		want   int
	}{
		{"rising", 1},
		{"falling", -1},
		{"sideways", 0},
	}
	for _, c := range cases {
		n := RegimeSource(func() string { return c.regime })
		out, _ := n.Eval(nil)
		if d := out["direction"].Direction(); d != c.want {
			t.Errorf("regime %q -> direction %d, want %d", c.regime, d, c.want)
		}
	}
}

func TestSentimentSource(t *testing.T) {
	n := SentimentSource("BTCUSDT", func(string) float64 { return 65 })
	out, _ := n.Eval(nil)
	if out["score"].Value != 65 {
		t.Errorf("sentiment = %v, want 65", out["score"].Value)
	}
}

func TestCatalogHasSources(t *testing.T) {
	r := Catalog(nil)
	for _, id := range []string{"source.price", "source.volume", "source.flow", "source.ta", "source.regime", "source.sentiment"} {
		if _, ok := r.Get(id); !ok {
			t.Errorf("catalog missing %q", id)
		}
	}
}
