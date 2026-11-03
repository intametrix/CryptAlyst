package main

import (
	"os"
	"runtime"
	"testing"
)

// The GPU probe must not claim a device when there is no way to interrogate
// one, and on non-Windows it must rely on PATH alone.
func TestNvidiaSmiPathIsSafe(t *testing.T) {
	got := nvidiaSmiPath()
	if got == "" {
		// No driver here: the honest answer is "no tool", never a fake path.
		if runtime.GOOS == "windows" {
			t.Log("no nvidia-smi on this Windows box")
		}
		return
	}
	if _, err := os.Stat(got); err != nil {
		t.Fatalf("nvidiaSmiPath returned %q which does not exist: %v", got, err)
	}
}
