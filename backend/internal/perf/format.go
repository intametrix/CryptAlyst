package perf

import "strconv"

func trimFloat(v float64, digits int) string {
	return strconv.FormatFloat(v, 'f', digits, 64)
}
