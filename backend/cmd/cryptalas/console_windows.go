//go:build windows

package main

import (
	"syscall"
	"unsafe"
)

var (
	kernel32           = syscall.NewLazyDLL("kernel32.dll")
	procSetConsoleMode = kernel32.NewProc("SetConsoleMode")
)

const enableVirtualTerminal = 0x0004

// enableANSIConsole turns on virtual-terminal processing so ANSI colour escapes
// render in cmd.exe / PowerShell. On Windows this is OFF by default, which is
// why a colour TTY shows as plain text unless this is enabled.
func enableANSIConsole() error {
	handle, err := syscall.Open("CONOUT$", syscall.O_RDWR, 0)
	if err != nil {
		return err
	}
	defer syscall.Close(handle)

	var mode uint32
	if err := syscall.GetConsoleMode(handle, &mode); err != nil {
		return err
	}
	mode |= enableVirtualTerminal
	_, _, err = procSetConsoleMode.Call(
		uintptr(handle),
		uintptr(mode),
	)
	if err != nil && err != syscall.Errno(0) {
		return err
	}
	// Also set on STD_ERROR_HANDLE so log lines shown there render too.
	_, _, _ = procSetConsoleMode.Call(
		uintptr(syscall.Stderr),
		uintptr(mode),
	)
	_ = unsafe.Pointer(nil)
	return nil
}
