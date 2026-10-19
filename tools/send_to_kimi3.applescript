-- Send a message to the interactive kimi TUI running in the Terminal tab whose
-- tty is /dev/ttys003 (cwd ~/Dev/Binance).
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
-- Run:  osascript tools/send_to_kimi3.applescript

property targetTty : "/dev/ttys003"
property messageText : "UPDATE from the other agent: the websocket bug is FOUND and FIXED, so skip section one of KIMI_TASK2.md entirely. Root cause was a Go trap: encoding/json matches object keys to struct tags CASE-INSENSITIVELY, so the string key e (event type) collided with my json:E int64 field and EVERY unmarshal returned a type error, discarding all ticks; separately the key Q (best-bid quantity) case-insensitively overwrote q (24h quote volume), which would have silently corrupted every baseline and liquidity floor even after the first bug was fixed. I replaced tagged structs with exact-key map decoding. The live pipeline now reports 3086 stream messages and a populated flow leaderboard (BTC 243837 dollars per 10s at 4.6x). Please now do ONLY the second half of KIMI_TASK2.md and write your answer to /Users/andrewpreston/Dev/Binance/KIMI_ANSWERS2.md: confirm or retract ADX as a soft score component instead of a veto, give me the precise volume-confirmation rule including the degenerate-median case, confirm the three numbers (liquidity floor 5M, risk per unit 0.5 percent, 2 units per correlation cluster), and state the BTC regime gate exactly (computation and action). No code, precise numbers."

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
delay 0.6
tell application "Terminal" to activate
delay 0.2
tell application "System Events"
	key code 36 -- Return
end tell

return "SENT to " & targetTty & " (with explicit Enter)"
