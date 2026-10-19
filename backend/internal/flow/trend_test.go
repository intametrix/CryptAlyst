package flow

import "testing"

func TestReadTrend(t *testing.T) {
	cases := []struct {
		name             string
		c3, c5, c10, c15 float64
		wantSign         int
		wantLabel        string
	}{
		{"all four up", 0.5, 0.8, 1.2, 1.6, 1, "sustained pump"},
		{"all four down", -0.5, -0.8, -1.2, -1.6, -1, "sustained dump"},
		{"flat", 0, 0, 0, 0, 0, "flat"},
		{"noise below deadband", 0.01, -0.01, 0.005, -0.005, 0, "flat"},
		{"3s alone", 2.0, 0.01, 0.01, 0.01, 1, "single-horizon up"},
		{"15s dominant down, 3s up", 0.4, -0.2, -0.9, -1.4, -1, "building down"},
	}
	for _, c := range cases {
		sign, agreement, label := readTrend(c.c3, c.c5, c.c10, c.c15)
		if sign != c.wantSign || label != c.wantLabel {
			t.Errorf("%s: got sign=%d label=%q agreement=%.3f, want sign=%d label=%q",
				c.name, sign, label, agreement, c.wantSign, c.wantLabel)
		}
	}
}
