-- Generic sender: type a message into the interactive kimi TUI and submit it.
--
-- Usage:
--   osascript tools/kimi_send.applescript "message text" ["/dev/ttys003"]
--
-- Always ends with an EXPLICIT Enter.
--
-- `do script ... in tab` types the text followed by a newline, which most TUIs
-- accept as "submit". That is not reliable enough on its own: a TUI reads raw
-- key events, and some builds treat the newline as a line-feed inside the input
-- buffer rather than as a carriage-return submit. So after typing we focus the
-- session and post a real Return key code (36) through System Events. If the
-- newline already submitted, the extra Return lands on an empty prompt, which
-- is harmless; if it did not, this is what actually sends the message.
--
-- One line only: a multi-line paste would submit several partial prompts.

on run argv
	set targetTty to "/dev/ttys003"
	if (count of argv) is 0 then
		return "ERROR: no message supplied"
	end if
	set messageText to item 1 of argv
	if (count of argv) > 1 then set targetTty to item 2 of argv

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
	end tell

	-- Explicit Enter, after a beat to let the TUI finish ingesting the paste.
	delay 0.7
	tell application "Terminal" to activate
	delay 0.2
	tell application "System Events"
		key code 36 -- Return
	end tell

	return "SENT to " & targetTty & " (with explicit Enter)"
end run
