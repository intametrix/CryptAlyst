-- Send a message to the interactive kimi TUI and GUARANTEE it submits.
--
-- Usage:
--   osascript tools/kimi_send.applescript "message text" ["/dev/ttys003"]
--
-- Why this verifies rather than assumes:
--
-- The original version typed the text and posted a single Return (key code 36)
-- after a fixed delay. That works most of the time, and "most of the time" is
-- exactly the problem: when it fails, the message sits unsent in the input box
-- while the sender reports success. That is the same silent-failure shape as a
-- watcher that is dead but looks idle -- the sender cannot tell the difference
-- between delivered and parked.
--
-- So this version types, presses Return, then READS BACK the terminal to see
-- whether the input line still holds the message, and presses Return again if
-- it does. Up to three attempts. It returns "SENT" only when the text is gone
-- from the input line, and "UNCONFIRMED" when it is still there, so a caller can
-- never mistake a parked message for a delivered one.
--
-- Always one line: a multi-line paste would submit several partial prompts.

on lastLines(theText, n)
	set AppleScript's text item delimiters to linefeed
	set theLines to text items of theText
	set AppleScript's text item delimiters to ""
	if (count of theLines) > n then
		set theLines to items ((count of theLines) - n + 1) thru -1 of theLines
	end if
	set AppleScript's text item delimiters to linefeed
	set out to theLines as text
	set AppleScript's text item delimiters to ""
	return out
end lastLines

on run argv
	set targetTty to "/dev/ttys003"
	if (count of argv) is 0 then return "ERROR: no message supplied"
	set messageText to item 1 of argv
	if (count of argv) > 1 then set targetTty to item 2 of argv

	-- A short distinctive fragment to look for when verifying.
	set probe to messageText
	if (count of characters of probe) > 40 then
		set probe to characters 1 thru 40 of probe as text
	end if

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
		if foundTab is missing value then return "NOT FOUND: no tab with tty " & targetTty
		set selected tab of foundWindow to foundTab
		set frontmost of foundWindow to true
		do script messageText in foundTab
	end tell

	delay 1.0
	tell application "Terminal" to activate
	delay 0.2

	repeat with attempt from 1 to 3
		tell application "System Events" to key code 36 -- Return
		delay 0.8
		-- Read back: is the probe still sitting in the visible input area?
		set stillThere to false
		try
			tell application "Terminal"
				set visibleText to contents of foundTab
			end tell
			set tailText to my lastLines(visibleText, 4)
			if tailText contains probe then set stillThere to true
		end try
		if stillThere is false then
			return "SENT to " & targetTty & " (confirmed after " & attempt & " Return)"
		end if
	end repeat

	return "UNCONFIRMED to " & targetTty & " - text still visible in the input box after 3 Returns"
end run
