package main

import (
	"encoding/json"
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
			OnboardDate: 1567965300000,
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
	seed, skipped := buildSeed(spotFixture(), fapiFixture(), map[string]bool{"USDT": true, "FDUSD": true})

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
	seed, _ := buildSeed(spotFixture(), fapiFixture(), map[string]bool{"USDT": true, "FDUSD": true})
	for i := 1; i < len(seed.Instruments); i++ {
		a, b := seed.Instruments[i-1], seed.Instruments[i]
		if a.Class > b.Class || (a.Class == b.Class && a.Symbol > b.Symbol) {
			t.Fatalf("instruments not sorted at %d: %s after %s", i, b.ID, a.ID)
		}
	}
}

func TestMergeSeedKeepsHandCuratedRecords(t *testing.T) {
	seed, _ := buildSeed(spotFixture(), fapiFixture(), map[string]bool{"USDT": true})
	existing := &seedFile{
		Instruments: []refdata.Instrument{
			{ID: "crypto_perp:binance_usdt_m:spcxbusdt", Class: refdata.ClassCryptoPerp,
				Symbol: "SPCXBUSDT", Name: "SpaceX Beta USDT-M Perpetual", Status: "testing",
				Venues: []string{"binance_usdt_m"}},
		},
		Documents: []refdata.Document{
			{ID: "doc-hand-note", Title: "hand-written note", Kind: "note", Body: "keep me"},
		},
		Edges: []refdata.Edge{
			{Subject: "crypto_perp:binance_usdt_m:spcxbusdt", Predicate: "listed_on", Object: "venue:binance_usdt_m"},
		},
	}
	docs := generatedDocs(map[string]int{"perp": 1, "spot": 1}, []string{"USDT"}, time.Unix(0, 0))
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
