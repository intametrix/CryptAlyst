package fastsample

import "testing"

func TestLabelBarrier(t *testing.T) {
	cases := []struct {
		name    string
		entry   float64
		price   float64
		band    float64
		want    string
		wantHit bool
	}{
		{"up", 100, 100.20, 0.10, "UP", true},
		{"reversal", 100, 99.80, 0.10, "REVERSAL", true},
		{"neither", 100, 100.05, 0.10, "NEITHER", false},
		{"zero entry", 0, 100, 0.10, "", false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got, hit := LabelBarrier(c.entry, c.price, c.band)
			if got != c.want || hit != c.wantHit {
				t.Fatalf("LabelBarrier(%v,%v,%v)=%q,%v want %q,%v",
					c.entry, c.price, c.band, got, hit, c.want, c.wantHit)
			}
		})
	}
}
