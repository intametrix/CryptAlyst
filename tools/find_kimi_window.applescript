-- Enumerate Terminal windows/tabs and show the tail of each tab's scrollback,
-- so we can identify which one is hosting the interactive kimi TUI.
tell application "Terminal"
	set out to ""
	set winIndex to 0
	repeat with w in windows
		set winIndex to winIndex + 1
		set tabIndex to 0
		repeat with t in tabs of w
			set tabIndex to tabIndex + 1
			set h to ""
			try
				set h to (history of t)
			end try
			if (count of h) > 1400 then
				set h to text -1400 thru -1 of h
			end if
			set out to out & "=== win " & winIndex & " tab " & tabIndex & " tty=" & (tty of t) & " busy=" & (busy of t) & " procs=" & (processes of t) & " ===" & return & h & return & return
		end repeat
	end repeat
	return out
end tell
