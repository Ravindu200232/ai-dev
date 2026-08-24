//go:build windows

package core

import "testing"

// Real netstat output, which is the only thing worth testing this against: the
// columns are space aligned, the addresses take several shapes, and a port
// number appears on both sides of a connected socket.
const netstatSample = `
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1204
  TCP    127.0.0.1:5173         0.0.0.0:0              LISTENING       19656
  TCP    127.0.0.1:7824         0.0.0.0:0              LISTENING       20284
  TCP    127.0.0.1:52001        127.0.0.1:5173         ESTABLISHED     8123
  TCP    [::]:3000              [::]:0                 LISTENING       19656
`

func TestPIDFromNetstat(t *testing.T) {
	if got := pidFromNetstat(netstatSample, 5173); got != 19656 {
		t.Errorf("port 5173 is held by 19656, got %d", got)
	}
	if got := pidFromNetstat(netstatSample, 7824); got != 20284 {
		t.Errorf("port 7824 is held by 20284, got %d", got)
	}
	// An IPv6 listener is still a listener holding the port.
	if got := pidFromNetstat(netstatSample, 3000); got != 19656 {
		t.Errorf("port 3000 is held by 19656, got %d", got)
	}
	// Nothing is listening on 9999, and the established socket to 5173 must
	// not be read as one: killing that pid would stop an innocent client.
	if got := pidFromNetstat(netstatSample, 9999); got != 0 {
		t.Errorf("nothing holds 9999, got %d", got)
	}
	if got := pidFromNetstat(netstatSample, 52001); got != 0 {
		t.Errorf("an established socket is not a listener, got %d", got)
	}
}
