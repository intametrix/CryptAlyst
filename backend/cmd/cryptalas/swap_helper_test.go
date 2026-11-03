package main

import (
	"strings"
	"testing"
)

// The Windows self-update helper must show brief progress, use absolute paths
// only (so it works from any working directory), and restart the agent.
func TestSwapHelperScript(t *testing.T) {
	const (
		exe     = `C:\Users\dev\AppData\Local\CryptALAS\cryptalas.exe`
		newPath = exe + ".new"
		version = "2.0.0-a348efa"
		from    = "f3f0bb4"
	)
	got := swapHelperScript(exe, newPath, version, from, []string{"-name", "R1"})

	// 1. Absolute paths only: the staged file and the target are both full
	//    Windows paths, so the helper never depends on the current directory.
	if !strings.Contains(got, `"`+newPath+`"`) {
		t.Fatalf("helper does not reference the staged exe by absolute path:\n%s", got)
	}
	if !strings.Contains(got, `"`+exe+`"`) {
		t.Fatalf("helper does not reference the installed exe by absolute path:\n%s", got)
	}

	// 2. It reports progress: the new version and the old one are both shown.
	if !strings.Contains(got, "updating to "+version) {
		t.Fatalf("helper does not announce the target version:\n%s", got)
	}
	if !strings.Contains(got, "from "+from) {
		t.Fatalf("helper does not show the version it is replacing:\n%s", got)
	}
	if !strings.Contains(got, "installing...") || !strings.Contains(got, "done - restarting agent") {
		t.Fatalf("helper is missing its progress steps:\n%s", got)
	}

	// 3. It waits for the old process to exit before moving the file, so the
	//    locked-exe problem on Windows is handled rather than raced.
	waitAt := strings.Index(got, ":wait")
	moveAt := strings.Index(got, "move /Y")
	if waitAt < 0 || moveAt < 0 || waitAt > moveAt {
		t.Fatalf("helper does not wait for the process to exit before swapping:\n%s", got)
	}
	if !strings.Contains(got, "tasklist") || !strings.Contains(got, "goto wait") {
		t.Fatalf("helper has no wait-for-exit loop:\n%s", got)
	}
	// tasklist's IMAGENAME filter matches the image NAME only; passing a full
	// path there never matches, so the wait would be skipped entirely.
	if !strings.Contains(got, `IMAGENAME eq cryptalas.exe`) {
		t.Fatalf("tasklist filter is not the bare image name:\n%s", got)
	}
	if strings.Contains(got, `IMAGENAME eq C:\`) {
		t.Fatalf("tasklist filter wrongly contains a full path:\n%s", got)
	}

	// 4. It restarts the agent with the same arguments it was running with.
	if !strings.Contains(got, `start "" "`+exe+`" -name R1`) {
		t.Fatalf("helper does not restart the agent with its arguments:\n%s", got)
	}

	// 5. It is a CRLF batch file that closes itself.
	if !strings.Contains(got, "\r\n") {
		t.Fatalf("helper script is not CRLF-terminated (cmd.exe needs CRLF):\n%q", got)
	}
	if !strings.HasSuffix(got, "exit") {
		t.Fatalf("helper does not exit when finished:\n%s", got)
	}

	// 5b. No doubled backslashes: Go string quoting would emit C:\\Users\\...
	// which cmd.exe takes literally and the move fails. This was a real bug.
	if strings.Contains(got, `\\`) {
		t.Fatalf("helper contains escaped backslashes that cmd.exe cannot resolve:\n%s", got)
	}

	// 6. A failed install must not silently look like success.
	if !strings.Contains(got, "install FAILED") {
		t.Fatalf("helper does not surface a failed install:\n%s", got)
	}
}
