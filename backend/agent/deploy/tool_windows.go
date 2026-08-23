//go:build windows

package deploy

import (
	"os/exec"
	"syscall"
)

// detach gives an interactive sign-in a console of its own. Without it the
// tool's prompts go to a window nobody can see.
func detach(command *exec.Cmd) {
	command.SysProcAttr = &syscall.SysProcAttr{CreationFlags: 0x00000010} // CREATE_NEW_CONSOLE
}
