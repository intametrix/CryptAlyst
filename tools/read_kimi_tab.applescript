-- Read the tail of the scrollback for a given Terminal tab tty.
on run argv
	set targetTty to "/dev/ttys003"
	if (count of argv) > 0 then set targetTty to item 1 of argv
	set tailChars to 4000
	if (count of argv) > 1 then set tailChars to (item 2 of argv) as integer

	tell application "Terminal"
		repeat with w in windows
			repeat with t in tabs of w
				try
					if (tty of t) is targetTty then
						set h to (history of t)
						if (count of h) > tailChars then
							set h to text -tailChars thru -1 of h
						end if
						return h
					end if
				end try
			end repeat
		end repeat
		return "NOT FOUND: " & targetTty
	end tell
end run
