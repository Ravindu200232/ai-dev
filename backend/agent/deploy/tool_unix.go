//go:build !windows

package deploy

import "os/exec"

// detach is a Windows concern: elsewhere the tool inherits the terminal the
// application was started from, which is where the customer is looking.
func detach(_ *exec.Cmd) {}
