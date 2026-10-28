//go:build !windows

package main

import "os/exec"

// enableANSIConsole is a no-op on non-Windows platforms: terminals already
// render ANSI colour natively.
func enableANSIConsole() error { return nil }

// detachProcess is a no-op off Windows.
func detachProcess(cmd *exec.Cmd) {}
