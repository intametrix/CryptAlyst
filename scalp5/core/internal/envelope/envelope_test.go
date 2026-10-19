package envelope

import (
	"encoding/json"
	"testing"
	"time"
)

func TestRoundTrip(t *testing.T) {
	type pay struct {
		Symbol string  `json:"symbol"`
		Value  float64 `json:"value"`
	}
	env := New(pay{"BTCUSDT", 0.42}, Meta{Plane: PlaneIntel, Topic: "intel.flow", Key: "BTCUSDT",
		Producer: "signals", Generation: 7})
	raw, err := json.Marshal(env)
	if err != nil {
		t.Fatal(err)
	}
	got, err := Decode[pay](raw)
	if err != nil {
		t.Fatal(err)
	}
	if got.Data.Symbol != "BTCUSDT" || got.Meta.Generation != 7 {
		t.Fatalf("round trip lost data: %+v", got)
	}
	if got.Meta.Version != CurrentVersion {
		t.Fatalf("version not stamped: %d", got.Meta.Version)
	}
}

func TestDecodeRefusesUnknownMember(t *testing.T) {
	_, err := Decode[map[string]any]([]byte(`{"data":{},"meta":{},"surprise":1}`))
	if err == nil {
		t.Fatal("expected refusal of an unknown top-level member")
	}
}

func TestDecodeIgnoresUnknownMetaKey(t *testing.T) {
	_, err := Decode[map[string]any]([]byte(`{"data":{},"meta":{"version":1,"future_field":"x"}}`))
	if err != nil {
		t.Fatalf("meta must be extensible: %v", err)
	}
}

func TestVersionPolicy(t *testing.T) {
	if err := AcceptVersion(CurrentVersion + 1); err == nil {
		t.Fatal("must refuse a newer revision")
	}
	if err := AcceptVersion(0); err != nil {
		t.Fatalf("absent version is revision 1: %v", err)
	}
}

func TestRefsAccumulate(t *testing.T) {
	env := New(map[string]any{"x": 1}, Meta{})
	env = env.WithRef(Ref{Topic: "ticks", Generation: 3}).WithRef(Ref{Topic: "candles", Generation: 3})
	if len(env.Meta.Refs) != 2 {
		t.Fatalf("refs = %d, want 2", len(env.Meta.Refs))
	}
}

func TestAge(t *testing.T) {
	old := time.Now().Add(-2 * time.Second)
	env := New(1, Meta{ComputedAt: &old})
	if age := env.AgeMS(time.Now()); age < 1900 || age > 2600 {
		t.Fatalf("age = %d ms, want about 2000", age)
	}
}

func TestPipe(t *testing.T) {
	env := New(1, Meta{})
	env = Pipe(env, func(e Envelope[int]) Envelope[int] {
		e.Meta.Extra = map[string]string{"seen": "yes"}
		return e
	})
	if env.Meta.Extra["seen"] != "yes" {
		t.Fatal("transformer did not run")
	}
}
