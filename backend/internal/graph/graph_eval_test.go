package graph

import (
	"strings"
	"testing"
)

// sourceNode is a fixed-value test source: it emits one signal and ignores its
// (empty) inputs.
type sourceNode struct {
	id  string
	typ SignalType
	sig Signal
}

func (s sourceNode) ID() string     { return s.id }
func (s sourceNode) Title() string  { return s.id }
func (s sourceNode) Kind() Kind     { return Source }
func (s sourceNode) Inputs() []Port { return nil }
func (s sourceNode) Outputs() []Port {
	return []Port{{Name: "out", Dir: Output, Type: s.typ}}
}
func (s sourceNode) Eval(map[string]Signal) (map[string]Signal, error) {
	return map[string]Signal{"out": s.sig}, nil
}

// passNode is an identity transform with matching input/output types, used to
// build a valid two-node cycle.
type passNode struct{ id string }

func (p passNode) ID() string    { return p.id }
func (p passNode) Title() string { return p.id }
func (p passNode) Kind() Kind    { return Transform }
func (p passNode) Inputs() []Port {
	return []Port{{Name: "in", Dir: Input, Type: TypeScore}}
}
func (p passNode) Outputs() []Port {
	return []Port{{Name: "out", Dir: Output, Type: TypeScore}}
}
func (p passNode) Eval(in map[string]Signal) (map[string]Signal, error) {
	if s, ok := in["in"]; ok {
		return map[string]Signal{"out": s}, nil
	}
	return map[string]Signal{}, nil
}

// sinkNode captures its input for assertion.
type sinkNode struct {
	id   string
	typ  SignalType
	got  Signal
	call int
}

func (s *sinkNode) ID() string    { return s.id }
func (s *sinkNode) Title() string { return s.id }
func (s *sinkNode) Kind() Kind    { return Sink }
func (s *sinkNode) Inputs() []Port {
	return []Port{{Name: "in", Dir: Input, Type: s.typ}}
}
func (s *sinkNode) Outputs() []Port { return nil }
func (s *sinkNode) Eval(in map[string]Signal) (map[string]Signal, error) {
	if v, ok := in["in"]; ok {
		s.got = v
	}
	s.call++
	return map[string]Signal{}, nil
}

func TestGraphEvaluateChain(t *testing.T) {
	g := NewGraph()
	src := sourceNode{id: "s", typ: TypeScore, sig: New(TypeScore, "BTCUSDT", 80, 10)}
	thr := ThresholdNode{Threshold: 60}
	snk := &sinkNode{id: "k", typ: TypeDirection}
	g.Add(src)
	g.Add(thr)
	g.Add(snk)
	g.Link(Link{ID: "l1", From: "s", FromPort: "out", To: "calc.threshold", ToPort: "score"})
	g.Link(Link{ID: "l2", From: "calc.threshold", FromPort: "direction", To: "k", ToPort: "in"})

	if err := g.Validate(); err != nil {
		t.Fatalf("Validate: %v", err)
	}
	if _, err := g.Evaluate(); err != nil {
		t.Fatalf("Evaluate: %v", err)
	}
	if snk.call != 1 {
		t.Errorf("sink called %d times, want 1", snk.call)
	}
	if d := snk.got.Direction(); d != 1 {
		t.Errorf("sink direction = %d, want 1 (score 80 >= threshold 60)", d)
	}
}

func TestGraphWeightedLink(t *testing.T) {
	g := NewGraph()
	src := sourceNode{id: "s", typ: TypeScore, sig: New(TypeScore, "BTCUSDT", 80, 10)}
	snk := &sinkNode{id: "k", typ: TypeScore}
	g.Add(src)
	g.Add(snk)
	g.Link(Link{ID: "l1", From: "s", FromPort: "out", To: "k", ToPort: "in", Weight: 0.5, Expr: "weight * score"})

	if _, err := g.Evaluate(); err != nil {
		t.Fatalf("Evaluate: %v", err)
	}
	if snk.got.Value != 40 {
		t.Errorf("sink value = %v, want 40 (0.5 * 80)", snk.got.Value)
	}
}

func TestGraphCycleDetection(t *testing.T) {
	g := NewGraph()
	g.Add(passNode{id: "a"})
	g.Add(passNode{id: "b"})
	g.Link(Link{ID: "l1", From: "a", FromPort: "out", To: "b", ToPort: "in"})
	g.Link(Link{ID: "l2", From: "b", FromPort: "out", To: "a", ToPort: "in"}) // a -> b -> a

	if err := g.Validate(); err == nil || !strings.Contains(err.Error(), "cycle") {
		t.Errorf("Validate() error = %v, want a cycle error", err)
	}
}

func TestGraphTypeMismatch(t *testing.T) {
	g := NewGraph()
	g.Add(sourceNode{id: "s", typ: TypeScore, sig: New(TypeScore, "X", 1, 0)})
	g.Add(&sinkNode{id: "k", typ: TypeDirection}) // expects DIRECTION, gets SCORE
	g.Link(Link{ID: "l1", From: "s", FromPort: "out", To: "k", ToPort: "in"})

	if err := g.Validate(); err == nil {
		t.Error("Validate() should reject a SCORE -> DIRECTION link")
	}
}

func TestGraphUnknownNode(t *testing.T) {
	g := NewGraph()
	g.Add(sourceNode{id: "s", typ: TypeScore, sig: New(TypeScore, "X", 1, 0)})
	g.Link(Link{ID: "l1", From: "s", FromPort: "out", To: "ghost", ToPort: "in"})
	if err := g.Validate(); err == nil {
		t.Error("Validate() should reject a link to an unknown node")
	}
}
