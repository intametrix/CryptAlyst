-- Send the task brief to the interactive kimi TUI running in the Terminal tab
-- whose tty is /dev/ttys003 (cwd ~/Dev/Binance).
--
-- The message is deliberately ONE line: the TUI submits on newline, so a
-- multi-line paste would fire several partial prompts. The brief itself lives
-- in a file that kimi is asked to read.

property targetTty : "/dev/ttys003"
property messageText : "Please read the file /Users/andrewpreston/Dev/Binance/KIMI_BRIEF.md in full, then follow the instructions in its 'What I want from you' section. You are the quantitative design partner on a Binance SPOT Turtle trading system: produce the written trading plan, the indicator critique, the failure modes, a concrete parameter table with numbers, a cheap validation procedure, and your top 5 next build steps. Write your answer to /Users/andrewpreston/Dev/Binance/KIMI_TRADING_PLAN.md. Be specific and numerate; do not write code; keep it under about 2500 words."

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

	-- Make it visible so the user can watch the exchange.
	set selected tab of foundWindow to foundTab
	set frontmost of foundWindow to true

	do script messageText in foundTab
	return "SENT to " & targetTty
end tell
