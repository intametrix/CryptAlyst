package heat

import "strconv"

// strconvFormat renders a float with a fixed number of decimals. Local so the
// heat package stays free of the other packages' formatting helpers.
func strconvFormat(v float64, digits int) string {
	return strconv.FormatFloat(v, 'f', digits, 64)
}
