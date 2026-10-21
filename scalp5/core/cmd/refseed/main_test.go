package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
	"time"

	"cryptalyst/core/internal/refdata"
)

func fapiFixture() fapiExchangeInfo {
	return fapiExchangeInfo{Symbols: []fapiSymbol{
		{
			Symbol: "BTCUSDT", Pair: "BTCUSDT", ContractType: "PERPETUAL", Status: "TRADING",
			BaseAsset: "BTC", QuoteAsset: "USDT", MarginAsset: "USDT",
			PricePrecision: 2, QuantityPrecision: 3, UnderlyingType: "COIN",
			OnboardDate:     1567965300000,
			MaintMarginPct:  "2.5000",
			LiquidationFee:  "0.012500",
			TriggerProtect:  "0.0500",
			MarketTakeBound: "0.05",
			Filters: []filter{
				{"filterType": json.RawMessage(`"PRICE_FILTER"`), "tickSize": json.RawMessage(`"0.10"`), "minPrice": json.RawMessage(`"556.80"`), "maxPrice": json.RawMessage(`"4529764"`)},
				{"filterType": json.RawMessage(`"LOT_SIZE"`), "stepSize": json.RawMessage(`"0.001"`), "minQty": json.RawMessage(`"0.001"`), "maxQty": json.RawMessage(`"1000"`)},
				{"filterType": json.RawMessage(`"MIN_NOTIONAL"`), "minNotional": json.RawMessage(`"100"`)},
			},
		},
		{
			Symbol: "BTCUSDT_251225", Pair: "BTCUSDT", ContractType: "CURRENT_QUARTER", Status: "TRADING",
			BaseAsset: "BTC", QuoteAsset: "USDT", MarginAsset: "USDT",
			Filters: []filter{
				{"filterType": json.RawMessage(`"PRICE_FILTER"`), "tickSize": json.RawMessage(`"0.10"`)},
				{"filterType": json.RawMessage(`"LOT_SIZE"`), "stepSize": json.RawMessage(`"0.001"`), "minQty": json.RawMessage(`"0.001"`), "maxQty": json.RawMessage(`"1000"`)},
			},
		},
		{
			Symbol: "DEADUSDT", ContractType: "PERPETUAL", Status: "SETTLING",
			BaseAsset: "DEAD", QuoteAsset: "USDT", MarginAsset: "USDT",
			Filters: []filter{
				{"filterType": json.RawMessage(`"PRICE_FILTER"`), "tickSize": json.RawMessage(`"0.01"`)},
				{"filterType": json.RawMessage(`"LOT_SIZE"`), "stepSize": json.RawMessage(`"1"`), "minQty": json.RawMessage(`"1"`), "maxQty": json.RawMessage(`"100"`)},
				{"filterType": json.RawMessage(`"MIN_NOTIONAL"`), "minNotional": json.RawMessage(`"5"`)},
			},
		},
	}}
}

func spotFixture() spotExchangeInfo {
	return spotExchangeInfo{Symbols: []spotSymbol{
		{
			Symbol: "BTCUSDT", Status: "TRADING", BaseAsset: "BTC", QuoteAsset: "USDT",
			IsSpotTradingAllowed: true,
			Filters: []filter{
				{"filterType": json.RawMessage(`"PRICE_FILTER"`), "tickSize": json.RawMessage(`"0.01"`)},
				{"filterType": json.RawMessage(`"LOT_SIZE"`), "stepSize": json.RawMessage(`"0.0001"`), "minQty": json.RawMessage(`"0.0001"`), "maxQty": json.RawMessage(`"100"`)},
				{"filterType": json.RawMessage(`"NOTIONAL"`), "minNotional": json.RawMessage(`"5"`)},
			},
		},
		{
			Symbol: "BTCFDUSD", Status: "TRADING", BaseAsset: "BTC", QuoteAsset: "FDUSD",
			IsSpotTradingAllowed: true,
			Filters: []filter{
				{"filterType": json.RawMessage(`"PRICE_FILTER"`), "tickSize": json.RawMessage(`"0.01"`)},
				{"filterType": json.RawMessage(`"LOT_SIZE"`), "stepSize": json.RawMessage(`"0.0001"`), "minQty": json.RawMessage(`"0.0001"`), "maxQty": json.RawMessage(`"100"`)},
			},
		},
		{
			// BNBBTC is excluded: quote not in the accepted set.
			Symbol: "BNBBTC", Status: "TRADING", BaseAsset: "BNB", QuoteAsset: "BTC",
			IsSpotTradingAllowed: true,
			Filters: []filter{
				{"filterType": json.RawMessage(`"PRICE_FILTER"`), "tickSize": json.RawMessage(`"0.000001"`)},
				{"filterType": json.RawMessage(`"LOT_SIZE"`), "stepSize": json.RawMessage(`"0.001"`), "minQty": json.RawMessage(`"0.001"`), "maxQty": json.RawMessage(`"10000"`)},
			},
		},
		{
			// Trading disabled: excluded regardless of quote asset.
			Symbol: "BROKENUSDT", Status: "BREAK", BaseAsset: "BROKEN", QuoteAsset: "USDT",
			IsSpotTradingAllowed: false,
			Filters: []filter{
				{"filterType": json.RawMessage(`"PRICE_FILTER"`), "tickSize": json.RawMessage(`"0.01"`)},
				{"filterType": json.RawMessage(`"LOT_SIZE"`), "stepSize": json.RawMessage(`"1"`), "minQty": json.RawMessage(`"1"`), "maxQty": json.RawMessage(`"9"`)},
			},
		},
	}}
}

func byID(t *testing.T, seed seedFile, id string) refdata.Instrument {
	t.Helper()
	for _, in := range seed.Instruments {
		if in.ID == id {
			return in
		}
	}
	t.Fatalf("instrument %s not in seed", id)
	return refdata.Instrument{}
}

func TestBuildSeed(t *testing.T) {
	seed, skipped := buildSeed(spotFixture(), fapiFixture(), map[string]bool{"USDT": true, "FDUSD": true}, nil)

	// One TRADING perp; the quarterly and the settling perp are skipped.
	if got := len(seed.Instruments); got != 3 {
		t.Fatalf("instruments: got %d, want 3 (1 perp + 2 spot)", got)
	}
	if skipped["fapi contract CURRENT_QUARTER"] != 1 || skipped["fapi status SETTLING"] != 1 {
		t.Fatalf("skipped perps: %+v", skipped)
	}
	if skipped["spot quote BTC"] != 1 || skipped["spot status BREAK"] != 1 {
		t.Fatalf("skipped spot: %+v", skipped)
	}

	perp := byID(t, seed, "crypto_perp:binance_usdt_m:btcusdt")
	if perp.Class != refdata.ClassCryptoPerp || perp.Status != refdata.StatusActive {
		t.Fatalf("perp class/status: %+v", perp)
	}
	if perp.Limits["tick_size"] != 0.10 || perp.Limits["lot_size"] != 0.001 ||
		perp.Limits["min_qty"] != 0.001 || perp.Limits["max_qty"] != 1000 ||
		perp.Limits["min_notional"] != 100 ||
		perp.Limits["price_precision"] != 2 || perp.Limits["quantity_precision"] != 3 {
		t.Fatalf("perp limits: %+v", perp.Limits)
	}
	if perp.Meta["margin_asset"] != "USDT" || perp.Meta["contract_type"] != "PERPETUAL" ||
		perp.Meta["underlying_type"] != "COIN" || perp.Meta["base_asset"] != "BTC" {
		t.Fatalf("perp meta: %+v", perp.Meta)
	}
	if perp.Meta["onboard_date"] != time.UnixMilli(1567965300000).UTC().Format(time.RFC3339) {
		t.Fatalf("perp onboard_date: %q", perp.Meta["onboard_date"])
	}
	if perp.Identifiers["ticker"] != "BTC" || perp.Identifiers["exchange_symbol"] != "BTCUSDT" {
		t.Fatalf("perp identifiers: %+v", perp.Identifiers)
	}

	spot := byID(t, seed, "crypto_spot:binance:btcusdt")
	if spot.Limits["tick_size"] != 0.01 || spot.Limits["lot_size"] != 0.0001 ||
		spot.Limits["min_notional"] != 5 {
		t.Fatalf("spot limits: %+v", spot.Limits)
	}
	if spot.Meta["base_asset"] != "BTC" || spot.Meta["quote_asset"] != "USDT" {
		t.Fatalf("spot meta: %+v", spot.Meta)
	}
	byID(t, seed, "crypto_spot:binance:btcfdusd")

	// One listed_on edge per instrument, pointing at the right venue.
	if got := len(seed.Edges); got != 3 {
		t.Fatalf("edges: got %d, want 3", got)
	}
	for _, e := range seed.Edges {
		if e.Predicate != refdata.EdgeListedOn {
			t.Fatalf("edge predicate: %+v", e)
		}
		want := "venue:binance"
		if e.Subject == perp.ID {
			want = "venue:binance_usdt_m"
		}
		if e.Object != want {
			t.Fatalf("edge %s -> %s, want %s", e.Subject, e.Object, want)
		}
	}
}

func TestBuildSeedSortOrder(t *testing.T) {
	seed, _ := buildSeed(spotFixture(), fapiFixture(), map[string]bool{"USDT": true, "FDUSD": true}, nil)
	for i := 1; i < len(seed.Instruments); i++ {
		a, b := seed.Instruments[i-1], seed.Instruments[i]
		if a.Class > b.Class || (a.Class == b.Class && a.Symbol > b.Symbol) {
			t.Fatalf("instruments not sorted at %d: %s after %s", i, b.ID, a.ID)
		}
	}
}

func TestMergeSeedKeepsHandCuratedRecords(t *testing.T) {
	seed, _ := buildSeed(spotFixture(), fapiFixture(), map[string]bool{"USDT": true}, nil)
	existing := &seedFile{
		Instruments: []refdata.Instrument{
			{ID: "crypto_perp:binance_usdt_m:spcxbusdt", Class: refdata.ClassCryptoPerp,
				Symbol: "SPCXBUSDT", Name: "SpaceX Beta USDT-M Perpetual", Status: "testing",
				Venues: []string{"binance_usdt_m"}},
		},
		Documents: []refdata.Document{
			{ID: "doc-hand-note", Title: "hand-written note", Kind: "note", Body: "keep me"},
			// Same title+kind as a generated doc: must survive exactly once.
			{ID: "doc-old-spot-def", Title: "crypto_spot class definition", Kind: "definition", Body: "older version\n"},
		},
		Edges: []refdata.Edge{
			{Subject: "crypto_perp:binance_usdt_m:spcxbusdt", Predicate: "listed_on", Object: "venue:binance_usdt_m"},
		},
	}
	docs := generatedDocs(map[string]int{"perp": 1, "spot": 1}, []string{"USDT"}, time.Unix(0, 0), []string{"test"})
	mergeSeed(&seed, existing, docs)

	byID(t, seed, "crypto_perp:binance_usdt_m:spcxbusdt") // survived the merge
	byID(t, seed, "crypto_perp:binance_usdt_m:btcusdt")   // generated record present

	// Generated docs added, existing doc kept, no duplicate title+kind.
	seen := map[string]int{}
	for _, d := range seed.Documents {
		seen[d.Title+"\x00"+d.Kind]++
	}
	for k, n := range seen {
		if n > 1 {
			t.Fatalf("duplicate doc %q", k)
		}
	}
	if seen["hand-written note\x00note"] != 1 || seen["crypto_spot class definition\x00definition"] != 1 {
		t.Fatalf("docs after merge: %+v", seen)
	}

	// Edge union: no duplicate triples.
	triples := map[string]int{}
	for _, e := range seed.Edges {
		triples[e.Subject+"\x00"+e.Predicate+"\x00"+e.Object]++
	}
	for k, n := range triples {
		if n > 1 {
			t.Fatalf("duplicate edge %q", k)
		}
	}
	if triples["crypto_perp:binance_usdt_m:spcxbusdt\x00listed_on\x00venue:binance_usdt_m"] != 1 {
		t.Fatalf("curated edge lost: %+v", triples)
	}
}

func TestParseQuotes(t *testing.T) {
	got := parseQuotes(" usdt, fdusd ,,EUR ")
	for _, want := range []string{"USDT", "FDUSD", "EUR"} {
		if !got[want] {
			t.Fatalf("parseQuotes missing %s: %+v", want, got)
		}
	}
	if len(got) != 3 {
		t.Fatalf("parseQuotes: %+v", got)
	}
}

func TestFilterStr(t *testing.T) {
	f := filter{"tickSize": json.RawMessage(`"0.10"`), "limit": json.RawMessage(`10`), "bad": json.RawMessage(`{"x"}`)}
	if got := f.str("tickSize"); got != "0.10" {
		t.Fatalf("string value: %q", got)
	}
	if got := f.str("limit"); got != "10" {
		t.Fatalf("number value: %q", got)
	}
	if got := f.str("missing"); got != "" {
		t.Fatalf("missing key: %q", got)
	}
	if got := f.str("bad"); got != "" {
		t.Fatalf("unreadable value: %q", got)
	}
}

func testEnrichment() *enrichment {
	return &enrichment{
		Assets: map[string]assetInfo{
			"BTC": {
				Name:   "Bitcoin",
				Supply: "19806450",
				Tags:   "Payments, Store Of Value",
				Link:   "https://www.binance.com/en/price/bitcoin",
				Logo:   "https://public.bnbstatic.com/image/pm/xxx/btc.png",
			},
		},
		Perp24: map[string]ticker24{
			"BTCUSDT": {Symbol: "BTCUSDT", OpenPrice: "100", HighPrice: "110", LowPrice: "95", LastPrice: "108", Volume: "12345.6", QuoteVolume: "1300000", PriceChangePercent: "8.0", Count: 999},
		},
		Spot24: map[string]ticker24{
			"BTCUSDT": {Symbol: "BTCUSDT", OpenPrice: "101", HighPrice: "109", LowPrice: "96", LastPrice: "105", Volume: "5000", QuoteVolume: "520000", PriceChangePercent: "4.0", Count: 111},
		},
		Premium: map[string]premium{
			"BTCUSDT": {Symbol: "BTCUSDT", MarkPrice: "107.9", IndexPrice: "108.0", LastFundingRate: "0.0001", NextFundingTime: 1792569600000},
		},
		Brackets: map[string]bracketInfo{
			"BTCUSDT": {MaxLeverage: 125, MaxNotional: 500000000},
		},
		Ratios: map[string]ratioInfo{
			"BTCUSDT": {LongShort: "2.5", TakerBuySell: "1.1", GlobalLongStr: "3.0"},
		},
	}
}

func TestEnrichmentApplied(t *testing.T) {
	e := testEnrichment()
	perp := perpInstrument(fapiFixture().Symbols[0], e)

	if perp.Name != "Bitcoin USDT-M Perpetual" {
		t.Fatalf("enriched name: %q", perp.Name)
	}
	// Risk limits: exchangeInfo fractions plus the bracket ladder.
	for k, want := range map[string]float64{
		"maint_margin": 2.5, "liquidation_fee": 0.0125, "trigger_protect": 0.05,
		"market_take_bound": 0.05, "max_leverage": 125, "max_notional": 500000000,
	} {
		if perp.Limits[k] != want {
			t.Fatalf("perp limits[%s] = %v, want %v (all: %+v)", k, perp.Limits[k], want, perp.Limits)
		}
	}
	for k, want := range map[string]string{
		"asset_name":              "Bitcoin",
		"asset_supply":            "19806450",
		"asset_tags":              "Payments, Store Of Value",
		"coin_link":               "https://www.binance.com/en/price/bitcoin",
		"mark_price":              "107.9",
		"index_price":             "108.0",
		"funding_rate":            "0.0001",
		"d24_last":                "108",
		"d24_quote_volume":        "1300000",
		"long_short_ratio":        "2.5",
		"global_long_short_ratio": "3.0",
	} {
		if perp.Meta[k] != want {
			t.Fatalf("perp meta[%s] = %q, want %q", k, perp.Meta[k], want)
		}
	}
	if perp.Meta["next_funding_time"] != time.UnixMilli(1792569600000).UTC().Format(time.RFC3339) {
		t.Fatalf("next_funding_time: %q", perp.Meta["next_funding_time"])
	}

	spot := spotInstrument(spotFixture().Symbols[0], e)
	if spot.Name != "Bitcoin/USDT Spot" {
		t.Fatalf("enriched spot name: %q", spot.Name)
	}
	if spot.Meta["d24_last"] != "105" {
		t.Fatalf("spot d24_last: %q", spot.Meta["d24_last"])
	}
	if _, isPerpField := spot.Meta["funding_rate"]; isPerpField {
		t.Fatal("spot must not carry perp-only fields")
	}
	if _, hasLeverage := spot.Limits["max_leverage"]; hasLeverage {
		t.Fatal("spot must not carry perp-only limits")
	}
}

func TestSignedGet(t *testing.T) {
	var gotKey, gotQuery string
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotKey = r.Header.Get("X-MBX-APIKEY")
		gotQuery = r.URL.RawQuery
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"ok":true}`)
	}))
	defer ts.Close()
	var dst map[string]any
	if err := signedGet(ts.URL, "/bracket", "KEY", "SECRET", &dst); err != nil {
		t.Fatalf("signedGet: %v", err)
	}
	if gotKey != "KEY" {
		t.Fatalf("api key header: %q", gotKey)
	}
	q, err := url.ParseQuery(gotQuery)
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	if q.Get("timestamp") == "" {
		t.Fatal("no timestamp in signed query")
	}
	sig := q.Get("signature")
	q.Del("signature")
	mac := hmac.New(sha256.New, []byte("SECRET"))
	mac.Write([]byte(q.Encode()))
	if sig != hex.EncodeToString(mac.Sum(nil)) {
		t.Fatalf("signature mismatch: got %q want %q over %q", sig, hex.EncodeToString(mac.Sum(nil)), q.Encode())
	}
}
