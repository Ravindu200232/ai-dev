//go:build !windows

package core

import (
	"os/exec"
	"syscall"
)

// A long-running command gets a process group of its own, so that stopping it
// stops everything it started.
//
// `npm run dev` is a launcher: it spawns the real server as a child and waits.
// Killing only the launcher leaves that child holding the port — and the next
// build, finding something listening, takes it for its own application and
// tests the previous one instead.

// ownGroup makes a command the leader of a new process group.
func ownGroup(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
}

// KillTree stops a command started by Shell.Start, and everything it started.
func KillTree(cmd *exec.Cmd) {
	if cmd == nil || cmd.Process == nil {
		return
	}
	// A negative pid is the group, and ownGroup made this process its leader,
	// so its pid is the group id.
	if syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL) != nil {
		_ = cmd.Process.Kill()
	}
}
