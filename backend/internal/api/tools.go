package api

import "net/http"

// handleTools serves the glossary that drives the dashboard's tooltips.
//
// It lives on the server rather than in the frontend on purpose: the same
// wording then describes an indicator in the chart, in a signal reason string,
// and in this documentation, so the three cannot drift apart. The UI caches it
// once and falls back to local copy if the request fails.
func (s *Server) handleTools(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"type":       "tools",
		"indicators": indicatorGlossary(),
		"metrics":    metricGlossary(),
	})
}

func indicatorGlossary() map[string]map[string]string {
	return map[string]map[string]string{
		"atr20": {
			"title":   "ATR(20) — Average True Range, the Turtles' N",
			"units":   "price",
			"formula": "Wilder-smoothed mean of True Range over 20 bars, where True Range = max(high-low, |high-prevClose|, |low-prevClose|)",
			"meaning": "The average distance price travels per bar, in price units. It is a volatility measure, not a direction.",
			"reading": "This single number sizes every position and sets every stop. A 2N stop is twice this value. Rising ATR means the market is becoming more dangerous, so the same dollar risk buys a smaller position.",
		},
		"atr_pct": {
			"title":   "ATR% — volatility as a share of price",
			"units":   "percent",
			"formula": "ATR(20) / close",
			"meaning": "The same volatility measure expressed as a fraction, which makes symbols comparable.",
			"reading": "Below about 1.5% a pair is dead capital; above about 25% a 2N stop is a coin flip and position sizing breaks on Binance lot steps. The trading band is 1.5%-25%.",
		},
		"adx14": {
			"title":   "ADX(14) — trend strength",
			"units":   "0-100",
			"formula": "Wilder-smoothed mean of DX, where DX = 100 x |+DI - -DI| / (+DI + -DI)",
			"meaning": "How strongly directional the market is, regardless of direction. Below ~20 is a range; above ~25 is a trend.",
			"reading": "Used as a soft score, deliberately never a veto. ADX is smoothed about three times over, so it lags fresh breakouts and reads high during blow-off tops -- exactly the worst entries. Use it to rank, not to gate.",
		},
		"plus_di": {
			"title":   "+DI — positive directional indicator",
			"units":   "0-100",
			"formula": "100 x Wilder-smoothed(+DM) / Wilder-smoothed(True Range)",
			"meaning": "Strength of upward directional movement over the period.",
			"reading": "+DI above -DI means buyers are in control. The gap between them is what the trend score uses.",
		},
		"minus_di": {
			"title":   "-DI — negative directional indicator",
			"units":   "0-100",
			"formula": "100 x Wilder-smoothed(-DM) / Wilder-smoothed(True Range)",
			"meaning": "Strength of downward directional movement over the period.",
			"reading": "-DI above +DI means sellers are in control.",
		},
		"rsi14": {
			"title":   "RSI(14) — Wilder's Relative Strength Index",
			"units":   "0-100",
			"formula": "100 - 100/(1 + average gain / average loss), Wilder-smoothed over 14 bars",
			"meaning": "Momentum as the balance of recent gains against losses.",
			"reading": "Mostly redundant for trend entries: RSI above 70 is where trend profits live, so blocking on it fights the system's nature. It earns its place only as a blow-off filter -- skip entries where RSI(1d) is above 85 and price is more than 1.6x its 200-day mean.",
		},
		"donchian20_upper": {
			"title":   "Donchian 20 upper — the breakout level",
			"units":   "price",
			"formula": "Highest high of the 20 completed bars before the current one",
			"meaning": "The level a close must exceed to be a System 1 long breakout.",
			"reading": "Levels come from COMPLETED bars only. Comparing a bar's high against a channel that contains that bar is a tautology -- it always equals the high -- so the entry level must come from the prior window.",
		},
		"donchian20_lower": {
			"title":   "Donchian 20 lower",
			"units":   "price",
			"formula": "Lowest low of the 20 completed bars before the current one",
			"meaning": "A close below this is an exit/avoid signal. Spot cannot short.",
			"reading": "Treat a downside break as information, not an entry.",
		},
		"donchian55_upper": {
			"title":   "Donchian 55 upper — System 2 breakout",
			"units":   "price",
			"formula": "Highest high of the 55 completed bars before the current one",
			"meaning": "The original Turtle slow system: fewer, better signals.",
			"reading": "System 2 breakouts always get taken; System 1 has a last-trade filter to avoid whipsaws.",
		},
		"donchian55_lower": {
			"title":   "Donchian 55 lower",
			"units":   "price",
			"formula": "Lowest low of the 55 completed bars before the current one",
			"meaning": "The 55-bar downside break. Also the level that decides the BTC regime gate.",
			"reading": "When BTC itself closes below this, new alt entries are blocked.",
		},
		"ema20": {
			"title":   "EMA(20) — 20-bar exponential moving average",
			"units":   "price",
			"formula": "Recursive average weighting recent closes more heavily; seeded with the SMA of the first 20 closes",
			"meaning": "A fast trend reference.",
			"reading": "EMA20 above EMA50 is a simple regime read, and is what the chart's trend badge uses.",
		},
		"ema50": {
			"title":   "EMA(50) — 50-bar exponential moving average",
			"units":   "price",
			"formula": "As EMA(20) but over 50 bars",
			"meaning": "A slower trend reference.",
			"reading": "The EMA20/EMA50 relationship is the chart's trend badge.",
		},
		"sma200": {
			"title":   "SMA(200) — 200-bar simple moving average",
			"units":   "price",
			"formula": "Arithmetic mean of the last 200 closes",
			"meaning": "The long-term reference level.",
			"reading": "Used for the blow-off filter: price more than 1.6x this AND RSI(1d) above 85 is a terminal pump leg, not a fresh trend.",
		},
		"volume_median_20": {
			"title":   "20-bar median volume",
			"units":   "quote currency",
			"formula": "Median quote volume of the 20 completed bars before the current one",
			"meaning": "This symbol's normal traded value per bar.",
			"reading": "A breakout bar must trade at least 1.5x this to be confirmed. The median is used rather than the mean because one prior spike would otherwise raise the bar for every later breakout.",
		},
	}
}

func metricGlossary() map[string]map[string]string {
	return map[string]map[string]string{
		"burst_multiple": {
			"title":   "Flow burst multiple",
			"units":   "x",
			"meaning": "Volume traded in the measurement window divided by THIS symbol's own baseline for that window. It is relative by design: 'a lot of volume' means nothing without knowing the symbol's normal.",
			"reading": "8x is the alert gate. 20x+ is exceptional. A high multiple on a near-zero baseline is noise, which is why an absolute dollar floor also applies.",
		},
		"burst_z": {
			"title":   "Flow burst z-score",
			"units":   "sigma",
			"meaning": "How many standard deviations the current window's flow sits above the symbol's own recent distribution.",
			"reading": "4 sigma is the gate. A symbol with a naturally volatile volume history needs a bigger multiple to reach the same z, and that is the point.",
		},
		"flow_quote": {
			"title":   "Flow — quote volume in the window",
			"units":   "USD",
			"meaning": "The actual money traded in the last window, measured by differencing Binance's cumulative 24h counters.",
			"reading": "This is the raw 'volume is flowing' number. It has a $5,000 absolute floor so a near-zero baseline cannot manufacture a huge multiple.",
		},
		"baseline_quote": {
			"title":   "Baseline flow for the window",
			"units":   "USD",
			"meaning": "The divisor the burst multiple is measured against: the lower quartile of recent window flows, floored at a fraction of the 24h average rate.",
			"reading": "The lower quartile is used, not the median. A median would be dragged upward by the very bursts being measured, ratcheting the baseline until the symbol goes blind.",
		},
		"coverage": {
			"title":   "Tick coverage",
			"units":   "0-1",
			"meaning": "The fraction of the window actually observed in received ticks.",
			"reading": "Below 0.7 the measurement is refused. This stops a 'burst' built from two messages spanning nine seconds from being scored.",
		},
		"taker_buy_ratio": {
			"title":   "Taker buy ratio",
			"units":   "0-1",
			"meaning": "Share of volume where the taker was the BUYER, i.e. someone lifting the offer. Binance's aggTrade 'm' flag is true when the buyer was the maker, meaning the taker sold.",
			"reading": "Above 0.55 supports a pump, below 0.45 supports a dump. It separates aggressive market buying from a wick on a thin book.",
		},
		"change_10s": {
			"title":   "Price change over the window",
			"units":   "percent",
			"meaning": "How far price moved across the measurement window.",
			"reading": "Must be at least 0.4% and at least 3 sigma of the symbol's own window volatility. Volume without a price move is not an onset.",
		},
		"sigma_multiple": {
			"title":   "Move in units of volatility",
			"units":   "x",
			"meaning": "The window's price change divided by the symbol's short-horizon volatility.",
			"reading": "3x is the gate. This is what makes the same percentage move mean different things on a stable pair and a jumpy one.",
		},
		"acceleration": {
			"title":   "Flow acceleration",
			"units":   "x",
			"meaning": "Current window flow divided by the immediately preceding window's flow.",
			"reading": "Above 1.0 means volume is still building -- the signature of a start rather than a tail. Below 1.0 is penalised in the score.",
		},
		"score": {
			"title":   "Alert score",
			"units":   "0-100",
			"meaning": "Weighted sum of four capped axes: flow burst 30, speed 25, taker aggression 25, quality 20, minus a lateness penalty.",
			"reading": "Under 55 is WATCH, 55-75 is ALERT, 75+ is CRITICAL. Every component is reported on the alert so the score can be audited rather than trusted.",
		},
		"severity": {
			"title":   "Severity",
			"units":   "enum",
			"meaning": "A banding of the score: WATCH, ALERT or CRITICAL.",
			"reading": "Set the minimum you want to see with the severity filter; WATCH shows everything that cleared the gates.",
		},
		"penalty": {
			"title":   "Lateness penalty",
			"units":   "points",
			"meaning": "Points subtracted when the 60s move already dwarfs the window move, or when flow is decelerating.",
			"reading": "A large penalty means you are looking at the tail of a move rather than its start.",
		},
		"range_position": {
			"title":   "Close position in window range",
			"units":   "0-1",
			"meaning": "Where price sits between the window's low and high. 1.0 is at the high.",
			"reading": "A pump should close near the top of its own window and a dump near the bottom. A burst that spikes and immediately reverses is a wick, not a move.",
		},
		"spread_pct": {
			"title":   "Spread",
			"units":   "percent",
			"meaning": "Best ask minus best bid, as a share of mid price.",
			"reading": "Above 0.4% the symbol is rejected. A wide spread means the 'move' may be a thin-book artefact rather than real demand.",
		},
		"book_depth_usd": {
			"title":   "Top-of-book depth",
			"units":   "USD",
			"meaning": "Notional resting on the thinner side of the best bid/offer.",
			"reading": "Below $500 the symbol is rejected as too thin to trust.",
		},
		"trades_10s": {
			"title":   "Trades in the window",
			"units":   "count",
			"meaning": "Number of trades in the measurement window.",
			"reading": "At least 8 are required. A 'burst' made of three prints is not a pump.",
		},
		"agg_confirmed": {
			"title":   "Taker-confirmed",
			"units":   "bool",
			"meaning": "Whether trade-level data agreed with the alert's direction.",
			"reading": "When enabled, the onset engine escalates a candidate to its individual trade stream for 180s to measure real taker imbalance.",
		},
		"extension_up_atr": {
			"title":   "Extension beyond the level",
			"units":   "N (ATR units)",
			"meaning": "How far price has moved past the Donchian breakout level, measured in ATR units.",
			"reading": "Above 2N the setup is rejected as chasing. A fresh breakout should be a small fraction of N beyond the level.",
		},
		"volume_ratio": {
			"title":   "Breakout volume ratio",
			"units":   "x",
			"meaning": "The breakout bar's quote volume divided by the median of the preceding 20 bars.",
			"reading": "Must clear 1.5x AND trade at least $150,000 absolute. The gate fails closed: a dust-volume breakout off dust-volume history is rejected, which is exactly the wick-hunt it exists to kill.",
		},
		"close_position": {
			"title":   "Close position in bar range",
			"units":   "0-1",
			"meaning": "Where the bar closed between its own low and high.",
			"reading": "A pump that closes on its low is being sold into, and the structure score is cut to 40%.",
		},
		"vol_ratio": {
			"title":   "Screen volume ratio",
			"units":   "x",
			"meaning": "The last 5 completed minutes of quote volume versus the symbol's own median 5-minute volume.",
			"reading": "3x is the screen gate. Values above 6x clear the z-score gate on their own.",
		},
		"trend": {
			"title":   "Trend",
			"units":   "enum",
			"meaning": "EMA20 versus EMA50 on the selected interval: up, down or flat.",
			"reading": "A quick regime read. Under 0.2% separation is reported as flat rather than pretending to a direction.",
		},
	}
}
