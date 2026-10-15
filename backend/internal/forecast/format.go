package forecast

import "strconv"

func strconvFormat(v float64, digits int) string {
	return strconv.FormatFloat(v, 'f', digits, 64)
}
