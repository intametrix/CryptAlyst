package models

import (
	"context"
	"testing"
)

// EVERY MODEL GETS A FIXTURE THAT PROVES IT CAN FIRE, and one that proves it
// declines. The reason is a lesson this project paid for twice: a metric that can
// never fire is indistinguishable from a metric that is correctly finding nothing.
//
// The multi-horizon agreement scored a lone horizon at 100% because the
// denominator was wrong, and the quiet-then-buy precursor had two conditions that
// were mutually contradictory so it could never fire at all. Both looked
// plausible and both were dead. A fixture that asserts "fires on a row built to
// make it fire" is the cheapest possible guard against repeating that.
//
// Four of these six models reported ZERO signals against the live market, which is
// exactly why these tests exist.

func fire(rows []Row) Snapshot { return Snapshot{AtMS: 1, Rows: rows} }

func TestVolatilityExpansionFiresAndDeclines(t *testing.T) {
	m := VolatilityExpansion{}
	hot := m.Evaluate(context.Background(), fire([]Row{
		{Symbol: "A", Price: 100, SigmaMultiple: 3.0, Change10s: -0.5},
	}))
	if len(hot) != 1 || hot[0].Direction != "DUMP" {
		t.Fatalf("expected one DUMP signal, got %d (%v)", len(hot), hot)
	}
	if hot[0].Score < 0.9 {
		t.Errorf("score %.3f should saturate at sigma 3.0", hot[0].Score)
	}
	quiet := m.Evaluate(context.Background(), fire([]Row{
		{Symbol: "A", Price: 100, SigmaMultiple: 0.8},
	}))
	if len(quiet) != 0 {
		t.Errorf("a calm symbol must not fire, got %d", len(quiet))
	}
}

func TestVolumeClimaxFiresAndDeclines(t *testing.T) {
	m := VolumeClimax{}
	hot := m.Evaluate(context.Background(), fire([]Row{
		{Symbol: "A", Price: 100, BurstMultiple: 6.0, TakerBuyRatio: 0.30},
	}))
	if len(hot) != 1 || hot[0].Direction != "DUMP" {
		t.Fatalf("expected one DUMP, got %d (%v)", len(hot), hot)
	}
	// A burst with BALANCED flow is volatility, not direction, so it must DECLINE
	// entirely. It used to emit a directionless signal, which produced 415 of them
	// in 54 ticks and drowned the models that were saying something actionable.
	balanced := m.Evaluate(context.Background(), fire([]Row{
		{Symbol: "A", Price: 100, BurstMultiple: 6.0, TakerBuyRatio: 0.50},
	}))
	if len(balanced) != 0 {
		t.Errorf("a directionless climax must not be emitted, got %d", len(balanced))
	}
	quiet := m.Evaluate(context.Background(), fire([]Row{{Symbol: "A", Price: 100, BurstMultiple: 1.0}}))
	if len(quiet) != 0 {
		t.Errorf("normal participation must not fire, got %d", len(quiet))
	}
}

func TestStructureBreakFiresAndDeclines(t *testing.T) {
	m := StructureBreak{}
	// Both horizons down, and price near the 24h low.
	hot := m.Evaluate(context.Background(), fire([]Row{
		{Symbol: "A", Price: 10, High24h: 20, Low24h: 10.1,
			Change10s: -0.5, Change60s: -0.9, RangePosition: 0.02},
	}))
	if len(hot) != 1 || hot[0].Direction != "DUMP" {
		t.Fatalf("expected one DUMP, got %d (%v)", len(hot), hot)
	}
	// Horizons disagreeing is a print, not progress.
	split := m.Evaluate(context.Background(), fire([]Row{
		{Symbol: "A", Price: 10, High24h: 20, Low24h: 9,
			Change10s: -0.5, Change60s: 0.9, RangePosition: 0.5},
	}))
	if len(split) != 0 {
		t.Errorf("disagreeing horizons must not fire, got %d", len(split))
	}
}

func TestMeanReversionFadesTheMove(t *testing.T) {
	m := MeanReversion{}
	// Strongly up, expanded range, closing at the high: the signal must be a
	// FADE, i.e. the OPPOSITE direction.
	hot := m.Evaluate(context.Background(), fire([]Row{
		{Symbol: "A", Price: 100, SigmaMultiple: 4.0, Change10s: 1.4, RangePosition: 0.99},
	}))
	if len(hot) != 1 {
		t.Fatalf("expected one signal, got %d", len(hot))
	}
	if hot[0].Direction != "DUMP" {
		t.Errorf("an overextended UP move must signal DUMP (a fade), got %s", hot[0].Direction)
	}
	quiet := m.Evaluate(context.Background(), fire([]Row{
		{Symbol: "A", Price: 100, SigmaMultiple: 1.0, Change10s: 0.05, RangePosition: 0.5},
	}))
	if len(quiet) != 0 {
		t.Errorf("a calm symbol must not fire, got %d", len(quiet))
	}
}

func TestMomentumDivergenceFiresAndDeclines(t *testing.T) {
	m := MomentumDivergence{}
	// Price UP while aggressive flow is SELLING: absorption.
	hot := m.Evaluate(context.Background(), fire([]Row{
		{Symbol: "A", Price: 100, Change10s: 0.5, TakerBuyRatio: 0.35, TakerQuote: 50000},
	}))
	if len(hot) != 1 {
		t.Fatalf("expected one signal, got %d", len(hot))
	}
	if hot[0].Direction != "DUMP" {
		t.Errorf("price up on selling must fade to DUMP, got %s", hot[0].Direction)
	}
	// Agreement must not fire.
	agree := m.Evaluate(context.Background(), fire([]Row{
		{Symbol: "A", Price: 100, Change10s: 0.5, TakerBuyRatio: 0.65, TakerQuote: 50000},
	}))
	if len(agree) != 0 {
		t.Errorf("price and flow agreeing must not fire, got %d", len(agree))
	}
}

func TestFlowImbalanceFiresAndDeclines(t *testing.T) {
	m := FlowImbalance{}
	hot := m.Evaluate(context.Background(), fire([]Row{
		{Symbol: "A", Price: 100, TakerBuyRatio: 0.25, TakerQuote: 50000},
	}))
	if len(hot) != 1 || hot[0].Direction != "DUMP" {
		t.Fatalf("expected one DUMP, got %d (%v)", len(hot), hot)
	}
	// An EMPTY taker buffer reads as 0.5 (balanced) and must decline. This is the
	// guard against the silent-zero failure that emptied two other features: if an
	// unmeasured ratio were 0, every symbol would look like maximum selling.
	empty := m.Evaluate(context.Background(), fire([]Row{
		{Symbol: "A", Price: 100, TakerBuyRatio: 0.5, TakerQuote: 0},
	}))
	if len(empty) != 0 {
		t.Errorf("an unmeasured buffer must not fire, got %d", len(empty))
	}
}

// Every registered model must have a non-zero interval, or its goroutine spins.
func TestAllModelsHaveIntervals(t *testing.T) {
	for _, m := range All() {
		if m.Interval() <= 0 {
			t.Errorf("%s has interval %v", m.Name(), m.Interval())
		}
		if m.Name() == "" {
			t.Error("a model must have a stable name; it is the signal's origin")
		}
	}
}
