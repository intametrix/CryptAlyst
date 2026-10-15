package api

import "net/http"

// The canonical palette. ONE source of truth for every directional and severity
// colour in the product.
//
// The chart markers, the alert-feed badges, the legend, the event list and the
// flow panels must all agree, because a reader learns the colour language once
// and then relies on it everywhere. The moment two of them disagree -- a green
// badge beside a red marker for the same event -- the language stops meaning
// anything and the user has to read the text to be sure. That defeats the point
// of colour.
//
// So the backend publishes the palette and stamps the resolved colour onto
// every element it emits. The UI renders what it is given rather than mapping
// enums to its own constants, which is the only way two independently written
// components cannot drift.
const (
	// Directional: green up, red down. These are the marker colours verbatim.
	ColorUp       = "#00e676"
	ColorDown     = "#ff1744"
	ColorReversal = "#ffab00"
	ColorNeutral  = "#40c4ff"

	// Severity is a separate axis and must not be confused with direction: a
	// CRITICAL dump and a WATCH pump are both red and green respectively.
	ColorWatch    = "#78909c"
	ColorAlert    = "#ffab00"
	ColorCritical = "#ff1744"
)

// Palette is the published colour language.
type Palette struct {
	Direction map[string]string `json:"direction"`
	Severity  map[string]string `json:"severity"`
	Letters   map[string]string `json:"letters"`
	Glyphs    map[string]string `json:"glyphs"`
	Note      string            `json:"note"`
}

// Palette returns the canonical colours and glyphs.
func PaletteFor() Palette {
	return Palette{
		Direction: map[string]string{
			"up":       ColorUp,
			"down":     ColorDown,
			"reversal": ColorReversal,
			"neutral":  ColorNeutral,
		},
		Severity: map[string]string{
			"WATCH":    ColorWatch,
			"ALERT":    ColorAlert,
			"CRITICAL": ColorCritical,
		},
		// The marker letters, which are also the legend and the feed icon text.
		Letters: map[string]string{
			"PUMP":           "P",
			"DUMP":           "D",
			"REVERSAL_PUMP":  "R",
			"REVERSAL_DUMP":  "R",
			"BREAKOUT_LONG":  "B",
			"BREAKOUT_SHORT": "B",
			"BREAKOUT":       "B",
		},
		Glyphs: map[string]string{
			"PUMP":           "arrowUp",
			"DUMP":           "arrowDown",
			"REVERSAL_PUMP":  "arrowUp",
			"REVERSAL_DUMP":  "arrowDown",
			"BREAKOUT_LONG":  "arrowUp",
			"BREAKOUT_SHORT": "arrowDown",
			"BREAKOUT":       "arrowUp",
		},
		Note: "Rendered by the backend onto every alert, mark and flow row as `color`; " +
			"the UI must use that value rather than mapping enums to its own constants.",
	}
}

// KindColor resolves a signal kind to its canonical colour.
func KindColor(kind string) string {
	switch kind {
	case "PUMP", "BREAKOUT_LONG":
		return ColorUp
	case "DUMP", "BREAKOUT_SHORT":
		return ColorDown
	case "REVERSAL_PUMP", "REVERSAL_DUMP":
		return ColorReversal
	default:
		return ColorNeutral
	}
}

// SeverityColor resolves a severity to its canonical colour.
func SeverityColor(severity string) string {
	switch severity {
	case "CRITICAL":
		return ColorCritical
	case "ALERT":
		return ColorAlert
	case "WATCH":
		return ColorWatch
	default:
		return ColorWatch
	}
}

// DirectionColor resolves a signed direction to its canonical colour.
func DirectionColor(direction int) string {
	if direction > 0 {
		return ColorUp
	}
	if direction < 0 {
		return ColorDown
	}
	return ColorNeutral
}

func (s *Server) handleColors(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, PaletteFor())
}
