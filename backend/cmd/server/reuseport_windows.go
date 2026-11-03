//go:build windows

package main

import "net"

// listenReusable on Windows has no SO_REUSEPORT equivalent in the standard
// library, so it falls back to a plain bind.
func listenReusable(addr string) (net.Listener, error) {
	return net.Listen("tcp", addr)
}
