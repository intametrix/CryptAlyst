package onset

import (
	"math"
	"strconv"
)

func strconvFormat(v float64, digits int) string {
	return strconv.FormatFloat(v, 'f', digits, 64)
}

// human renders a USD amount compactly: 1.23M / 412.0K / 1234.
func human(v float64) string {
	abs := math.Abs(v)
	switch {
	case abs >= 1e9:
		return strconvFormat(v/1e9, 2) + "B"
	case abs >= 1e6:
		return strconvFormat(v/1e6, 2) + "M"
	case abs >= 1e3:
		return strconvFormat(v/1e3, 1) + "K"
	default:
		return strconvFormat(v, 0)
	}
}
