//go:build !windows

package main

import (
	"context"
	"net"
	"syscall"
)

// listenReusable binds the address with SO_REUSEPORT where the platform allows
// it, so a freshly built server can take connections while the old process is
// still draining. That is what turns a restart into a handover: no window where
// the dashboard is unreachable, and no waiting for the slow startup work before
// the port exists again.
func listenReusable(addr string) (net.Listener, error) {
	lc := net.ListenConfig{
		Control: func(network, address string, c syscall.RawConn) error {
			var serr error
			err := c.Control(func(fd uintptr) {
				serr = syscall.SetsockoptInt(int(fd), syscall.SOL_SOCKET, syscall.SO_REUSEADDR, 1)
				if serr != nil {
					return
				}
				// SO_REUSEPORT is 0x200 on both Darwin and Linux.
				serr = syscall.SetsockoptInt(int(fd), syscall.SOL_SOCKET, 0x200, 1)
			})
			if err != nil {
				return err
			}
			return serr
		},
	}
	return lc.Listen(context.Background(), "tcp", addr)
}
