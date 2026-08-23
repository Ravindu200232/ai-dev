//go:build windows

package core

import (
	"os/exec"
	"strconv"
	"syscall"
)

// The Windows half of the note in process_unix.go. Windows has no process
// group that can be signalled as one, so the tree is stopped through taskkill,
// which is the only thing that reliably reaches a child of the launcher.

const createNewProcessGroup = 0x00000200

// ownGroup makes a command the root of a new process group.
func ownGroup(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: createNewProcessGroup}
}

// KillTree stops a command started by Shell.Start, and everything it started.
func KillTree(cmd *exec.Cmd) {
	if cmd == nil || cmd.Process == nil {
		return
	}
	kill := exec.Command("taskkill", "/T", "/F", "/PID", strconv.Itoa(cmd.Process.Pid))
	kill.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	if kill.Run() != nil {
		_ = cmd.Process.Kill()
	}
}
