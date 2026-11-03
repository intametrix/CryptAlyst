package main

import (
	"bytes"
	"encoding/json"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

// The agent must be able to run forecast/train jobs even when only the .exe
// was copied onto the box. Regression for the live failure:
//
//	python3: can't open file 'D:\tmp\dist\gpu\forecast.py'
func TestResolveRunnerFallsBackToEmbedded(t *testing.T) {
	// A state dir with no gpu/ folder and a working dir that also has none:
	// exactly the shape of an install where only the exe was copied.
	dir := t.TempDir()
	wd := t.TempDir()
	oldWD, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chdir(wd); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chdir(oldWD) })

	a := &Agent{state: dir}

	got, err := a.resolveRunner("forecast.py")
	if err != nil {
		t.Fatalf("resolveRunner failed with no runner on disk: %v", err)
	}
	if filepath.Base(got) != "forecast.py" {
		t.Fatalf("resolved to %q, want a forecast.py path", got)
	}
	// It must be a real, non-empty file that python3 can open.
	fi, err := os.Stat(got)
	if err != nil {
		t.Fatalf("resolved runner does not exist: %v", err)
	}
	if fi.Size() == 0 {
		t.Fatalf("resolved runner is empty")
	}
	body, err := os.ReadFile(got)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(body, []byte("def run_model")) || !bytes.Contains(body, []byte("def main(")) {
		t.Fatalf("materialised runner does not look like forecast.py")
	}

	// Asking again must reuse the cached copy rather than rewrite it.
	again, err := a.resolveRunner("forecast.py")
	if err != nil {
		t.Fatal(err)
	}
	if again != got {
		t.Fatalf("second resolve returned %q, want the cached %q", again, got)
	}
}

// A runner shipped on disk must win over the embedded copy.
func TestResolveRunnerPrefersDisk(t *testing.T) {
	root := t.TempDir()
	wd := t.TempDir()
	oldWD, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chdir(wd); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chdir(oldWD) })

	if err := os.MkdirAll(filepath.Join(root, "gpu"), 0o755); err != nil {
		t.Fatal(err)
	}
	marker := filepath.Join(root, "gpu", "forecast.py")
	if err := os.WriteFile(marker, []byte("# on-disk copy\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	// The exe dir is where the running binary lives; simulate by checking the
	// working-dir root, which is the second search location.
	if err := os.MkdirAll(filepath.Join(wd, "gpu"), 0o755); err != nil {
		t.Fatal(err)
	}
	diskPath := filepath.Join(wd, "gpu", "forecast.py")
	if err := os.WriteFile(diskPath, []byte("# on-disk copy\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	a := &Agent{state: t.TempDir()}
	got, err := a.resolveRunner("forecast.py")
	if err != nil {
		t.Fatal(err)
	}
	// Compare resolved paths: on macOS t.TempDir() lives under /var, which is a
	// symlink to /private/var, so raw string equality is not meaningful.
	gotReal, err := filepath.EvalSymlinks(got)
	if err != nil {
		t.Fatal(err)
	}
	wantReal, err := filepath.EvalSymlinks(diskPath)
	if err != nil {
		t.Fatal(err)
	}
	if gotReal != wantReal {
		t.Fatalf("resolveRunner returned %q, want the on-disk %q", gotReal, wantReal)
	}
}

// The embedded copies must stay byte-identical to the canonical scripts in
// backend/gpu/, so the two can never silently drift apart.
func TestEmbeddedRunnersMatchCanonicalScripts(t *testing.T) {
	for _, name := range []string{"forecast.py", "cuda_train.py"} {
		embedded, err := embeddedRunner(name)
		if err != nil {
			t.Fatalf("no embedded copy of %s: %v", name, err)
		}
		canonical, err := os.ReadFile(filepath.Join("..", "..", "gpu", name))
		if err != nil {
			t.Fatalf("cannot read canonical gpu/%s: %v", name, err)
		}
		if !bytes.Equal(embedded, canonical) {
			t.Fatalf("embedded runners/%s differs from gpu/%s — update the embedded copy", name, name)
		}
	}
}

// A cached runner written by an older build must be replaced, otherwise an
// agent update would keep executing the old script from the state dir.
func TestResolveRunnerRefreshesStaleCache(t *testing.T) {
	wd := t.TempDir()
	oldWD, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chdir(wd); err != nil { // no gpu/ here, so the cache is used
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chdir(oldWD) })

	state := t.TempDir()
	cached := filepath.Join(state, "gpu", "cuda_train.py")
	if err := os.MkdirAll(filepath.Dir(cached), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(cached, []byte("# stale runner from an older build\n"), 0o755); err != nil {
		t.Fatal(err)
	}

	a := &Agent{state: state}
	got, err := a.resolveRunner("cuda_train.py")
	if err != nil {
		t.Fatal(err)
	}
	body, err := os.ReadFile(got)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(body, []byte("stale runner")) {
		t.Fatalf("resolveRunner kept the stale cached runner")
	}
	want, err := embeddedRunner("cuda_train.py")
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(body, want) {
		t.Fatalf("cached runner was not refreshed from the embedded copy")
	}
}

// The shipped runner must actually execute every model the leaderboard can
// rank, and must report the device it used rather than a guess.
func TestForecastRunnerExecutesGrid(t *testing.T) {
	py, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("python3 not available on this host")
	}
	script := filepath.Join("runners", "forecast.py")
	series := make([]map[string]any, 60)
	for i := range series {
		series[i] = map[string]any{"t": 1_700_000_000_000 + i*3_600_000, "v": 100 + 5*math.Sin(float64(i)/6)}
	}

	for _, model := range []string{"naive", "mean", "sma20", "ema30", "trend", "trend50", "damped90", "ens_a", "analog5"} {
		payload := map[string]any{"payload": map[string]any{"series": series, "horizon": 4, "method": model}}
		body, _ := json.Marshal(payload)
		cmd := exec.Command(py, script)
		cmd.Stdin = bytes.NewReader(body)
		var out bytes.Buffer
		cmd.Stdout = &out
		cmd.Stderr = &out
		if err := cmd.Run(); err != nil {
			t.Fatalf("model %s: runner failed: %v (%s)", model, err, out.String())
		}
		var res map[string]any
		if err := json.Unmarshal(out.Bytes(), &res); err != nil {
			t.Fatalf("model %s: bad JSON %q", model, out.String())
		}
		if res["trained"] != true {
			t.Fatalf("model %s: not trained: %v", model, res)
		}
		fc, _ := res["forecast"].([]any)
		if len(fc) != 4 {
			t.Fatalf("model %s: returned %d points, want 4", model, len(fc))
		}
		if res["device"] == nil || res["compute"] == nil {
			t.Fatalf("model %s: device/compute not reported: %v", model, res)
		}
	}
}

// The optimiser must run on the agent and return real winning parameters.
func TestOptimizeRunnerFindsParameters(t *testing.T) {
	py, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("python3 not available on this host")
	}
	series := make([]map[string]any, 220)
	for i := range series {
		series[i] = map[string]any{"t": 1_700_000_000_000 + i*3_600_000, "v": 100 + 8*math.Sin(float64(i)/9)}
	}
	payload := map[string]any{"payload": map[string]any{"series": series, "horizon": 8, "windows": 4}}
	body, _ := json.Marshal(payload)
	cmd := exec.Command(py, filepath.Join("runners", "optimize.py"))
	cmd.Stdin = bytes.NewReader(body)
	var out bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &out
	if err := cmd.Run(); err != nil {
		t.Fatalf("optimizer failed: %v (%s)", err, out.String())
	}
	var res map[string]any
	if err := json.Unmarshal(out.Bytes(), &res); err != nil {
		t.Fatalf("bad JSON: %q", out.String())
	}
	if res["ok"] != true {
		t.Fatalf("optimizer did not report ok: %v", res)
	}
	if n, _ := res["evaluations"].(float64); n < 100 {
		t.Fatalf("only %v candidates evaluated", n)
	}
	if it, _ := res["iterations"].(float64); it < 1 {
		t.Fatalf("optimiser ran %v iterations", it)
	}
	if hist, _ := res["history"].([]any); len(hist) < 2 {
		t.Fatalf("no convergence history returned")
	}
	best, _ := res["best"].(map[string]any)
	params, _ := best["params"].(map[string]any)
	for _, k := range []string{"weights", "sma", "alpha", "damp"} {
		if params[k] == nil {
			t.Fatalf("winning params missing %q: %v", k, params)
		}
	}
	if res["device"] == nil || res["compute"] == nil {
		t.Fatalf("optimiser did not report the device: %v", res)
	}
}
