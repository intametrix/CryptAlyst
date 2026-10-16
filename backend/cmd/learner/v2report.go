package main

// Label v2 reporting: expectancy-in-R next to the v1 hit rate, never
// replacing it. Bracket for the 15min section is the v1 stop distance
// (per-prediction stop, default 1.0%); for the 10s section it is the
// label's own band_pct. Fees: symmetric round-trip taker cost,
// 2 × -fee_pct per bracket (default 0.1% per side).

import (
	"fmt"

	"pumpdump/backend/internal/ledger"
)

// v2Row is one sample for v2 aggregation, already in percent units.
type v2Row struct {
	mfe, mae, bracket float64
}

func v2Line(name string, rows []v2Row, feePct float64) string {
	if len(rows) == 0 {
		return fmt.Sprintf("%-18s n=0", name)
	}
	// Bracket is uniform within a cohort in practice; take the first.
	bracket := rows[0].bracket
	if bracket <= 0 {
		bracket = 1.0
	}
	samples := make([]ledger.ExcursionSample, len(rows))
	for i, r := range rows {
		samples[i] = ledger.ExcursionSample{MFEPct: r.mfe, MAEPct: r.mae}
	}
	st := ledger.ComputeRStats(samples, bracket, feePct)
	return fmt.Sprintf("%-18s n=%d medMFE=%+.3fR medMAE=%+.3fR P(MFE>=1R)=%.1f%% P(MAE>=1R)=%.1f%% E[R]gross=%+.3fR E[R]net=%+.3fR",
		name, st.N, st.MedianMFER, st.MedianMAER, 100*st.PMFE1R, 100*st.PMAE1R, st.GrossER, st.NetER)
}

// printV2_15min prints the v2 block for resolved 15min predictions,
// per tier and direction, alongside (after) the v1 section.
func printV2_15min(all []*prediction, feePct float64) {
	fmt.Printf("\nV2 (expectancy-in-R, bracket=1%% stop, fee %.3f%%/side, GROSS and NET-OF-FEES):\n", feePct)
	resolved := make([]*prediction, 0, len(all))
	for _, p := range all {
		if p.resolved() {
			resolved = append(resolved, p)
		}
	}
	collect := func(keep func(*prediction) bool) []v2Row {
		rows := make([]v2Row, 0, len(resolved))
		for _, p := range resolved {
			if keep != nil && !keep(p) {
				continue
			}
			bracket := p.stop
			if bracket <= 0 {
				bracket = 1.0
			}
			rows = append(rows, v2Row{mfe: p.mfe, mae: p.mae, bracket: bracket})
		}
		return rows
	}
	fmt.Println("  " + v2Line("overall", collect(nil), feePct))
	for _, sev := range []string{"CRITICAL", "ALERT", "WATCH"} {
		sev := sev
		fmt.Println("  " + v2Line("severity "+sev, collect(func(p *prediction) bool { return p.severity == sev }), feePct))
	}
	fmt.Println("  " + v2Line("direction LONG", collect(func(p *prediction) bool { return p.direction > 0 }), feePct))
	fmt.Println("  " + v2Line("direction SHORT", collect(func(p *prediction) bool { return p.direction < 0 }), feePct))
}
