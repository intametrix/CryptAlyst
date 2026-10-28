//go:build !windows

package main

// enableANSIConsole is a no-op on non-Windows platforms: terminals already
// render ANSI colour natively.
func enableANSIConsole() error { return nil }
