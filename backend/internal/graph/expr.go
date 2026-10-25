package graph

import (
	"fmt"
	"math"

	"github.com/expr-lang/expr"
)

// expr.go delegates equation evaluation to expr-lang/expr (the de-facto
// standard Go expression engine: bytecode-compiled, typed, sandboxed) rather
// than a hand-rolled parser. The environment is exactly the variable map the
// caller supplies, so a link equation is closed over a known set of names and
// cannot reach arbitrary code.
//
// The engine natively provides arithmetic, comparison, the `? :` ternary, and
// min/max. We add clamp and abs as small custom functions because they are
// table-stakes for signal transforms.
//
// For a hot path, compile once with expr.Compile and reuse the program; this
// convenience wrapper recompiles each call, which is fine for the designer and
// tests.
func Eval(s string, vars map[string]float64) (float64, error) {
	env := make(map[string]any, len(vars))
	for k, v := range vars {
		env[k] = v
	}
	prog, err := expr.Compile(s,
		expr.Env(env),
		expr.Function("clamp", clampFn),
		expr.Function("abs", absFn),
	)
	if err != nil {
		return 0, err
	}
	out, err := expr.Run(prog, env)
	if err != nil {
		return 0, err
	}
	f, ok := toNumber(out)
	if !ok {
		return 0, fmt.Errorf("equation result is not a number: %v (%T)", out, out)
	}
	if math.IsInf(f, 0) || math.IsNaN(f) {
		return 0, fmt.Errorf("equation result is not finite: %v", f)
	}
	return f, nil
}

// toNumber coerces expr's result into a float64. A bool becomes 1 or 0 so a
// bare comparison (a gate) is still a usable numeric signal.
func toNumber(v any) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	case bool:
		if n {
			return 1, true
		}
		return 0, true
	}
	return 0, false
}

func clampFn(params ...any) (any, error) {
	if len(params) != 3 {
		return nil, fmt.Errorf("clamp(x, lo, hi) needs 3 arguments")
	}
	x := toFloat(params[0])
	lo := toFloat(params[1])
	hi := toFloat(params[2])
	if x < lo {
		x = lo
	}
	if x > hi {
		x = hi
	}
	return x, nil
}

func absFn(params ...any) (any, error) {
	if len(params) != 1 {
		return nil, fmt.Errorf("abs(x) needs 1 argument")
	}
	x := toFloat(params[0])
	if x < 0 {
		x = -x
	}
	return x, nil
}

func toFloat(v any) float64 {
	switch n := v.(type) {
	case float64:
		return n
	case int:
		return float64(n)
	case int64:
		return float64(n)
	}
	return 0
}
