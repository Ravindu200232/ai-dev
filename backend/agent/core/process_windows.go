//go:build windows

package core

import (
	"os/exec"
	"regexp"
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

// listenerRow matches the netstat line for a socket in the LISTENING state,
// capturing the port it holds and the process holding it.
var listenerRow = regexp.MustCompile(`(?m)^\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)`)

// ReclaimPort stops whatever is listening on a port and reports whether it
// killed anything. A preview left running by an earlier build holds the port
// the next one needs, and the next one cannot bind while it does.
func ReclaimPort(port int) bool {
	pid := listenerPID(port)
	if pid <= 0 {
		return false
	}
	kill := exec.Command("taskkill", "/T", "/F", "/PID", strconv.Itoa(pid))
	kill.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	return kill.Run() == nil
}

// listenerPID is the process listening on a port, or 0 when none is.
func listenerPID(port int) int {
	netstat := exec.Command("netstat", "-ano", "-p", "TCP")
	netstat.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	out, err := netstat.Output()
	if err != nil {
		return 0
	}
	return pidFromNetstat(string(out), port)
}

// pidFromNetstat reads the process holding a port out of netstat's output.
// It is kept apart from running netstat so the parsing can be tested.
func pidFromNetstat(out string, port int) int {
	for _, row := range listenerRow.FindAllStringSubmatch(out, -1) {
		if held, err := strconv.Atoi(row[1]); err == nil && held == port {
			if pid, err := strconv.Atoi(row[2]); err == nil {
				return pid
			}
		}
	}
	return 0
}
