-- Send a one-line follow-up instruction to the interactive kimi TUI in the
-- Terminal tab whose tty is /dev/ttys003 (cwd ~/Dev/Binance).
--
-- One line only: the TUI submits on newline, so a multi-line paste would fire
-- several partial prompts. The detail lives in a file kimi is asked to read.

property targetTty : "/dev/ttys003"
property messageText : "New task: the stack is now a Go backend plus a React dashboard, and there is one concrete bug. Please read /Users/andrewpreston/Dev/Binance/KIMI_TASK2.md in full and do exactly what it asks: first diagnose why the Go combined-stream websocket client in backend/internal/binance/stream.go connects successfully but receives zero messages, giving a ranked list of candidate causes and the cheapest confirming experiment for each; then answer the parameter questions in the second half. Write your answer to /Users/andrewpreston/Dev/Binance/KIMI_ANSWERS2.md. Precise rules and numbers, no code, and keep it focused."

tell application "Terminal"
	set foundTab to missing value
	set foundWindow to missing value
	repeat with w in windows
		repeat with t in tabs of w
			try
				if (tty of t) is targetTty then
					set foundTab to t
					set foundWindow to w
					exit repeat
				end if
			end try
		end repeat
		if foundTab is not missing value then exit repeat
	end repeat

	if foundTab is missing value then
		return "NOT FOUND: no Terminal tab with tty " & targetTty
	end if

	set selected tab of foundWindow to foundTab
	set frontmost of foundWindow to true
	do script messageText in foundTab
	return "SENT to " & targetTty
end tell
