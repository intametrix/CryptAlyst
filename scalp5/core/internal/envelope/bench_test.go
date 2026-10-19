package envelope

import (
	"encoding/json"
	"testing"
	"time"
)

func benchBytes(b *testing.B) []byte {
	type pay struct {
		Symbol string  `json:"symbol"`
		Value  float64 `json:"value"`
	}
	now := time.Now().UTC()
	env := New(pay{"BTCUSDT", 0.42}, Meta{Plane: PlaneIntel, Topic: "intel.flow",
		Key: "BTCUSDT", Producer: "signals", Generation: 42, ComputedAt: &now})
	raw, _ := json.Marshal(env)
	return raw
}

func BenchmarkDecode(b *testing.B) {
	type pay struct {
		Symbol string  `json:"symbol"`
		Value  float64 `json:"value"`
	}
	raw := benchBytes(b)
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		if _, err := Decode[pay](raw); err != nil {
			b.Fatal(err)
		}
	}
}

func BenchmarkBuild(b *testing.B) {
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		env := New(map[string]float64{"v": 1}, Meta{Plane: PlaneMarket, Topic: "md.ticks"})
		_ = env.WithRef(Ref{Topic: "ticks", Generation: uint64(i)})
		if _, err := json.Marshal(env); err != nil {
			b.Fatal(err)
		}
	}
}
