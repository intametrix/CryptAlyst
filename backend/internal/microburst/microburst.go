// Package microburst loads the horizon-outcome models written by
// cmd/microburst and turns a flow feature map into per-horizon probabilities:
// P(upside-first), P(reversal-first) and P(neither).
//
// It is the runtime counterpart to the offline trainer, in the same way the
// outlook package is the runtime counterpart to cmd/learner: training stays
// offline, and this package only reads a versioned model file and applies it.
// A missing or unreadable model degrades to "no probabilities", never to a
// fabricated number.
package microburst

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"

	"pumpdump/backend/internal/ledger"
)

// Horizon is one horizon's three-way outcome distribution.
type Horizon struct {
	PUp       float64 `json:"p_up"`
	PReversal float64 `json:"p_reversal"`
	PNeither  float64 `json:"p_neither"`
}

// modelFile is the on-disk document written by cmd/microburst.
type modelFile struct {
	HorizonS int          `json:"horizon_s"`
	Up       ledger.Model `json:"up"`
	Reversal ledger.Model `json:"reversal"`
}

// Service holds the loaded per-horizon models.
type Service struct {
	horizons map[int]modelFile
}

// New loads every microburst_<N>s.json file in dir. An empty or absent dir
// yields an empty service (no probabilities), never an error, so the live
// server without a trained model keeps running and just omits the forecast.
func New(dir string) *Service {
	s := &Service{horizons: map[int]modelFile{}}
	if dir == "" {
		return s
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		return s
	}
	for _, e := range entries {
		if e.IsDir() || !strings.HasPrefix(e.Name(), "microburst_") || !strings.HasSuffix(e.Name(), ".json") {
			continue
		}
		data, err := os.ReadFile(filepath.Join(dir, e.Name()))
		if err != nil {
			continue
		}
		var mf modelFile
		if err := json.Unmarshal(data, &mf); err != nil {
			continue
		}
		if mf.HorizonS > 0 {
			s.horizons[mf.HorizonS] = mf
		}
	}
	return s
}

// Probability computes the per-horizon outcome distribution from a flat
// feature map (the same flow feature map captured at alert time). Horizons
// whose model cannot score these features are omitted.
func (s *Service) Probability(features map[string]float64) map[int]Horizon {
	out := map[int]Horizon{}
	for h, mf := range s.horizons {
		pUp, okUp := mf.Up.Predict(features)
		pRev, okRev := mf.Reversal.Predict(features)
		if !okUp || !okRev {
			continue
		}
		out[h] = Horizon{
			PUp:       pUp,
			PReversal: pRev,
			PNeither:  clamp(1-pUp-pRev, 0, 1),
		}
	}
	return out
}

func clamp(v, lo, hi float64) float64 {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}
