// Label v2: expectancy-in-R derived from the recorded MFE/MAE distribution,
// reported ALONGSIDE v1 (CORRECT/WRONG/EXPIRED at +1.5%/−1.0%/15min), never
// replacing it. See DESIGN.md. No trade simulation: v2 is a pure rescaling of
// the recorded direction-adjusted excursions into units of the risk bracket.

package ledger

// ExcursionSample is one resolution's recorded excursions in percent units
// (mfe_pct / mae_pct as stored in resolution records).
type ExcursionSample struct {
	MFEPct float64
	MAEPct float64
}

// RStats aggregates excursions in R units (1R = bracketPct of price move).
type RStats struct {
	N          int     `json:"n"`
	BracketPct float64 `json:"bracket_pct"`
	FeePct     float64 `json:"fee_pct"`

	MedianMFER float64 `json:"median_mfe_r"`
	MedianMAER float64 `json:"median_mae_r"`
	PMFE1R     float64 `json:"p_mfe_ge_1r"`
	PMAE1R     float64 `json:"p_mae_ge_1r"`
	MeanMFER   float64 `json:"mean_mfe_r"`
	MeanMAER   float64 `json:"mean_mae_r"`

	GrossER float64 `json:"e_r_gross"`
	NetER   float64 `json:"e_r_net"`
}

// ComputeRStats rescales recorded excursions into R and estimates gross and
// net expectancy. Gross E[R] = mean(MFE_R) − mean(MAE_R) — the expected
// favorable-minus-adverse excursion per signal, i.e. the gross drift of
// bracket-scaled exposure. Net subtracts a symmetric round-trip taker cost:
// 2 × feePct per bracket. An empty sample set yields zero stats.
func ComputeRStats(samples []ExcursionSample, bracketPct, feePct float64) RStats {
	st := RStats{N: len(samples), BracketPct: bracketPct, FeePct: feePct}
	if bracketPct <= 0 {
		bracketPct = 1.0
		st.BracketPct = 1.0
	}
	if len(samples) == 0 {
		return st
	}
	mfe := make([]float64, len(samples))
	mae := make([]float64, len(samples))
	for i, s := range samples {
		mfe[i] = s.MFEPct / bracketPct
		mae[i] = s.MAEPct / bracketPct
		st.MeanMFER += mfe[i]
		st.MeanMAER += mae[i]
		if mfe[i] >= 1 {
			st.PMFE1R++
		}
		if mae[i] >= 1 {
			st.PMAE1R++
		}
	}
	n := float64(len(samples))
	st.MeanMFER /= n
	st.MeanMAER /= n
	st.PMFE1R /= n
	st.PMAE1R /= n
	st.MedianMFER = median(mfe)
	st.MedianMAER = median(mae)
	st.GrossER = st.MeanMFER - st.MeanMAER
	st.NetER = st.GrossER - 2*feePct/bracketPct
	return st
}
