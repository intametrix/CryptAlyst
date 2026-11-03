package main

import (
	"bytes"
	"embed"
	"io/fs"
	"os"
	"path/filepath"
)

// The agent ships its own copies of the Python runners. A dist that only
// copies the .exe must still be able to run forecast/train jobs, so if the
// runner is not on disk next to the agent we materialise the embedded copy
// into the agent's state directory and run that instead.
//
//go:embed runners/forecast.py runners/cuda_train.py runners/optimize.py runners/timestral_runner.py runners/train_sequence.py runners/backtest_sweep.py
var embeddedRunners embed.FS

// embeddedRunner returns the source of a bundled runner by file name.
func embeddedRunner(name string) ([]byte, error) {
	return embeddedRunners.ReadFile("runners/" + name)
}

// resolveRunner finds a Python runner on disk, falling back to the embedded
// copy when the file was not shipped. It returns the path python3 should run.
//
// Search order: next to the executable, then the working directory (both under
// gpu/), then the state directory where the embedded copy is written.
func (a *Agent) resolveRunner(name string) (string, error) {
	var roots []string
	if exe, err := os.Executable(); err == nil {
		roots = append(roots, filepath.Dir(exe))
	}
	if wd, err := os.Getwd(); err == nil {
		roots = append(roots, wd)
	}
	for _, root := range roots {
		candidate := filepath.Join(root, "gpu", name)
		if fi, err := os.Stat(candidate); err == nil && !fi.IsDir() {
			return candidate, nil
		}
	}

	// Not shipped: write the embedded copy somewhere writable and use it.
	// The state dir is preferred; the OS temp dir is the backstop so a
	// read-only working directory can never block a job.
	candidates := []string{filepath.Join(a.state, "gpu", name)}
	candidates = append(candidates, filepath.Join(os.TempDir(), "cryptalas-runners", name))
	src, err := embeddedRunner(name)
	if err != nil {
		return "", err
	}
	var lastErr error
	for _, cached := range candidates {
		// Reuse the cached file only when it matches the copy built into this
		// binary. A stale cached runner would silently keep old behaviour after
		// an agent update, so it is rewritten whenever it differs.
		if current, err := os.ReadFile(cached); err == nil && bytes.Equal(current, src) {
			return cached, nil
		}
		if err := os.MkdirAll(filepath.Dir(cached), 0o755); err != nil {
			lastErr = err
			continue
		}
		if err := os.WriteFile(cached, src, 0o755); err != nil {
			lastErr = err
			continue
		}
		return cached, nil
	}
	if lastErr == nil {
		lastErr = fs.ErrNotExist
	}
	return "", lastErr
}

// runnerNames lists the bundled runners, for diagnostics.
func runnerNames() []string {
	entries, err := fs.ReadDir(embeddedRunners, "runners")
	if err != nil {
		return nil
	}
	out := make([]string, 0, len(entries))
	for _, e := range entries {
		out = append(out, e.Name())
	}
	return out
}
