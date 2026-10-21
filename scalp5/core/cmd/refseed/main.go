// Command refseed generates the refdata seed file from Binance exchangeInfo
// plus every bulk enrichment endpoint the exchange publishes: the spot and
// futures 24h tickers, funding / mark prices, the public asset catalog
// (names, supply, tags, coin-page links, logos), USDⓈ-M leverage brackets
// (signed, needs -fapi-key/-fapi-secret), and per-symbol long/short
// sentiment ratios.
//
// Two public endpoints anchor the registry: spot
// (api.binance.com/api/v3/exchangeInfo) and USDⓈ-M futures
// (fapi.binance.com/fapi/v1/exchangeInfo). Every TRADING USDT-M perpetual
// becomes a crypto_perp:binance_usdt_m record; every TRADING spot pair whose
// quote asset is in -spot-quotes (default USDT,FDUSD,USDC,EUR) becomes a
// crypto_spot:binance record.
//
// The existing seed file is merged, never replaced: hand-curated records that
// Binance does not mention (e.g. testing-marked contracts) survive, and
// documents/edges are unioned so nothing already in the registry is lost.
//
// Regenerate with:
//
//	go run ./cmd/refseed -out ./cmd/refdata/seed.json \
//	    -fapi-key "$BINANCE_FAPI_KEY" -fapi-secret "$BINANCE_FAPI_SECRET"
package main

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"cryptalyst/core/internal/refdata"
)

// ---------------------------------------------------------------- anchors
//
// filter is one exchange filter entry. Binance mostly serialises filter
// values as strings, but some (e.g. ICEBERG_PARTS limit) are bare numbers,
// so values are captured raw and read tolerantly.
type filter map[string]json.RawMessage

// str reads a filter field as a string, accepting JSON strings and numbers.
func (f filter) str(key string) string {
	raw, ok := f[key]
	if !ok {
		return ""
	}
	var s string
	if err := json.Unmarshal(raw, &s); err == nil {
		return s
	}
	var n json.Number
	if err := json.Unmarshal(raw, &n); err == nil {
		return n.String()
	}
	return ""
}

// spotSymbol is the slice of the spot exchangeInfo payload we consume.
type spotSymbol struct {
	Symbol               string   `json:"symbol"`
	Status               string   `json:"status"`
	BaseAsset            string   `json:"baseAsset"`
	QuoteAsset           string   `json:"quoteAsset"`
	IsSpotTradingAllowed bool     `json:"isSpotTradingAllowed"`
	Filters              []filter `json:"filters"`
}

type spotExchangeInfo struct {
	Symbols []spotSymbol `json:"symbols"`
}

// fapiSymbol is the slice of the USDT-M futures exchangeInfo we consume.
// The margin/risk fields (maintMarginPercent, liquidationFee, triggerProtect,
// marketTakeBound) are fractions the exchange applies to notional.
type fapiSymbol struct {
	Symbol            string   `json:"symbol"`
	Pair              string   `json:"pair"`
	ContractType      string   `json:"contractType"`
	Status            string   `json:"status"`
	BaseAsset         string   `json:"baseAsset"`
	QuoteAsset        string   `json:"quoteAsset"`
	MarginAsset       string   `json:"marginAsset"`
	PricePrecision    int      `json:"pricePrecision"`
	QuantityPrecision int      `json:"quantityPrecision"`
	UnderlyingType    string   `json:"underlyingType"`
	OnboardDate       int64    `json:"onboardDate"`
	MaintMarginPct    string   `json:"maintMarginPercent"`
	LiquidationFee    string   `json:"liquidationFee"`
	TriggerProtect    string   `json:"triggerProtect"`
	MarketTakeBound   string   `json:"marketTakeBound"`
	Filters           []filter `json:"filters"`
}

type fapiExchangeInfo struct {
	Symbols []fapiSymbol `json:"symbols"`
}

// seedFile mirrors cmd/refdata's seed shape so the output is drop-in.
type seedFile struct {
	Instruments []refdata.Instrument `json:"instruments"`
	Documents   []refdata.Document   `json:"documents"`
	Edges       []refdata.Edge       `json:"edges"`
}

func filterOf(filters []filter, typ string) filter {
	for _, f := range filters {
		if f.str("filterType") == typ {
			return f
		}
	}
	return nil
}

// fnum copies the first parseable field into dst[key].
func fnum(dst map[string]float64, key string, f filter, fields ...string) {
	if f == nil {
		return
	}
	for _, fld := range fields {
		if raw := f.str(fld); raw != "" {
			if v, err := strconv.ParseFloat(raw, 64); err == nil {
				dst[key] = v
				return
			}
		}
	}
}

// snum parses a bare string field into dst[key].
func snum(dst map[string]float64, key, raw string) {
	if raw == "" {
		return
	}
	if v, err := strconv.ParseFloat(raw, 64); err == nil {
		dst[key] = v
	}
}

func setMeta(m map[string]string, k, v string) {
	if v != "" {
		m[k] = v
	}
}

// ------------------------------------------------------------- enrichment
//
// enrichment is everything the generator learns beyond exchangeInfo,
// keyed for lookup while building instruments.
type enrichment struct {
	Assets   map[string]assetInfo   // base asset code -> coin info
	Perp24   map[string]ticker24    // perp symbol -> 24h stats
	Spot24   map[string]ticker24    // spot symbol -> 24h stats
	Premium  map[string]premium     // perp symbol -> funding / mark price
	Brackets map[string]bracketInfo // perp symbol -> leverage limits (signed)
	Ratios   map[string]ratioInfo   // perp symbol -> long/short sentiment
}

type assetInfo struct {
	Name      string
	QuoteName string
	Supply    string
	Tags      string
	Link      string
	Logo      string
}

// ticker24 is the union of the spot and futures 24h ticker fields.
type ticker24 struct {
	Symbol             string `json:"symbol"`
	PriceChangePercent string `json:"priceChangePercent"`
	OpenPrice          string `json:"openPrice"`
	HighPrice          string `json:"highPrice"`
	LowPrice           string `json:"lowPrice"`
	LastPrice          string `json:"lastPrice"`
	Volume             string `json:"volume"`
	QuoteVolume        string `json:"quoteVolume"`
	Count              int64  `json:"count"`
}

// premium is one fapi premiumIndex entry.
type premium struct {
	Symbol          string `json:"symbol"`
	MarkPrice       string `json:"markPrice"`
	IndexPrice      string `json:"indexPrice"`
	LastFundingRate string `json:"lastFundingRate"`
	NextFundingTime int64  `json:"nextFundingTime"`
}

// bracketInfo summarises one symbol's notional bracket ladder.
type bracketInfo struct {
	MaxLeverage float64 // initialLeverage of the first bracket
	MaxNotional float64 // notionalCap of the last bracket
}

// ratioInfo is the sentiment trio from the public /futures/data endpoints.
type ratioInfo struct {
	LongShort     string // top trader position long/short ratio
	TakerBuySell  string // taker buy/sell volume ratio
	GlobalLongStr string // global account long/short ratio
}

// product is one entry of the public website product catalog.
type product struct {
	Symbol    string   `json:"s"`
	Base      string   `json:"b"`
	Quote     string   `json:"q"`
	AssetName string   `json:"an"`
	QuoteName string   `json:"qn"`
	Supply    float64  `json:"cs"`
	Tags      []string `json:"tags"`
}

type productsResponse struct {
	Code string    `json:"code"`
	Data []product `json:"data"`
}

// assetDetail is one entry of the public asset catalog.
type assetDetail struct {
	AssetCode        string `json:"assetCode"`
	AssetDisplayName string `json:"assetDisplayName"`
	EnLink           string `json:"enLink"`
	LogoURL          string `json:"logoUrl"`
	FullLogoURL      string `json:"fullLogoUrl"`
}

type assetsResponse struct {
	Code string        `json:"code"`
	Data []assetDetail `json:"data"`
}

// ratioPoint is one row of a /futures/data ratio history.
type ratioPoint struct {
	Symbol         string `json:"symbol"`
	LongShortRatio string `json:"longShortRatio"`
	BuySellRatio   string `json:"buySellRatio"`
}

func (e *enrichment) asset(base string) (assetInfo, bool) {
	if e == nil {
		return assetInfo{}, false
	}
	a, ok := e.Assets[base]
	return a, ok
}

// applyAssetMeta copies coin-catalog facts into an instrument's meta.
func applyAssetMeta(m map[string]string, base string, e *enrichment) {
	a, ok := e.asset(base)
	if !ok {
		return
	}
	setMeta(m, "asset_name", a.Name)
	setMeta(m, "asset_tags", a.Tags)
	setMeta(m, "asset_supply", a.Supply)
	setMeta(m, "coin_link", a.Link)
	setMeta(m, "logo_url", a.Logo)
}

// applyTickerMeta copies the 24h snapshot into meta. Values are strings
// straight from the ticker; they describe the generation moment and go stale
// by design until the next regeneration.
func applyTickerMeta(m map[string]string, t ticker24, ok bool) {
	if !ok {
		return
	}
	setMeta(m, "d24_open", t.OpenPrice)
	setMeta(m, "d24_high", t.HighPrice)
	setMeta(m, "d24_low", t.LowPrice)
	setMeta(m, "d24_last", t.LastPrice)
	setMeta(m, "d24_volume", t.Volume)
	setMeta(m, "d24_quote_volume", t.QuoteVolume)
	setMeta(m, "d24_price_change_pct", t.PriceChangePercent)
	setMeta(m, "d24_trades", strconv.FormatInt(t.Count, 10))
}

// ------------------------------------------------------ instrument build

func perpInstrument(s fapiSymbol, e *enrichment) refdata.Instrument {
	lims := map[string]float64{}
	fnum(lims, "tick_size", filterOf(s.Filters, "PRICE_FILTER"), "tickSize")
	lot := filterOf(s.Filters, "LOT_SIZE")
	fnum(lims, "lot_size", lot, "stepSize")
	fnum(lims, "min_qty", lot, "minQty")
	fnum(lims, "max_qty", lot, "maxQty")
	fnum(lims, "min_notional", filterOf(s.Filters, "MIN_NOTIONAL"), "minNotional")
	lims["price_precision"] = float64(s.PricePrecision)
	lims["quantity_precision"] = float64(s.QuantityPrecision)
	snum(lims, "maint_margin", s.MaintMarginPct)
	snum(lims, "liquidation_fee", s.LiquidationFee)
	snum(lims, "trigger_protect", s.TriggerProtect)
	snum(lims, "market_take_bound", s.MarketTakeBound)
	meta := map[string]string{
		"base_asset":      s.BaseAsset,
		"quote_asset":     s.QuoteAsset,
		"margin_asset":    s.MarginAsset,
		"contract_type":   s.ContractType,
		"underlying_type": s.UnderlyingType,
		"source":          "binance:fapi/v1/exchangeInfo",
	}
	if s.OnboardDate > 0 {
		meta["onboard_date"] = time.UnixMilli(s.OnboardDate).UTC().Format(time.RFC3339)
	}
	if e != nil {
		applyAssetMeta(meta, s.BaseAsset, e)
		if b, ok := e.Brackets[s.Symbol]; ok {
			lims["max_leverage"] = b.MaxLeverage
			lims["max_notional"] = b.MaxNotional
		}
		if p, ok := e.Premium[s.Symbol]; ok {
			setMeta(meta, "mark_price", p.MarkPrice)
			setMeta(meta, "index_price", p.IndexPrice)
			setMeta(meta, "funding_rate", p.LastFundingRate)
			if p.NextFundingTime > 0 {
				meta["next_funding_time"] = time.UnixMilli(p.NextFundingTime).UTC().Format(time.RFC3339)
			}
		}
		if r, ok := e.Ratios[s.Symbol]; ok {
			setMeta(meta, "long_short_ratio", r.LongShort)
			setMeta(meta, "taker_buy_sell_ratio", r.TakerBuySell)
			setMeta(meta, "global_long_short_ratio", r.GlobalLongStr)
		}
		if t, ok := e.Perp24[s.Symbol]; ok {
			applyTickerMeta(meta, t, true)
		}
	}
	name := s.BaseAsset
	if a, ok := e.asset(s.BaseAsset); ok && a.Name != "" {
		name = a.Name
	}
	return refdata.Instrument{
		ID:     "crypto_perp:binance_usdt_m:" + strings.ToLower(s.Symbol),
		Class:  refdata.ClassCryptoPerp,
		Symbol: s.Symbol,
		Name:   name + " USDT-M Perpetual",
		Status: refdata.StatusActive,
		Venues: []string{"binance_usdt_m"},
		Identifiers: map[string]string{
			"ticker":          s.BaseAsset,
			"exchange_symbol": s.Symbol,
		},
		Limits: lims,
		Meta:   meta,
	}
}

func spotInstrument(s spotSymbol, e *enrichment) refdata.Instrument {
	lims := map[string]float64{}
	fnum(lims, "tick_size", filterOf(s.Filters, "PRICE_FILTER"), "tickSize")
	lot := filterOf(s.Filters, "LOT_SIZE")
	fnum(lims, "lot_size", lot, "stepSize")
	fnum(lims, "min_qty", lot, "minQty")
	fnum(lims, "max_qty", lot, "maxQty")
	// Newer spot listings use NOTIONAL, older ones MIN_NOTIONAL.
	fnum(lims, "min_notional", filterOf(s.Filters, "NOTIONAL"), "minNotional")
	fnum(lims, "min_notional", filterOf(s.Filters, "MIN_NOTIONAL"), "minNotional")
	meta := map[string]string{
		"base_asset":  s.BaseAsset,
		"quote_asset": s.QuoteAsset,
		"source":      "binance:api/v3/exchangeInfo",
	}
	if e != nil {
		applyAssetMeta(meta, s.BaseAsset, e)
		if t, ok := e.Spot24[s.Symbol]; ok {
			applyTickerMeta(meta, t, true)
		}
	}
	name := s.BaseAsset
	if a, ok := e.asset(s.BaseAsset); ok && a.Name != "" {
		name = a.Name
	}
	return refdata.Instrument{
		ID:     "crypto_spot:binance:" + strings.ToLower(s.Symbol),
		Class:  refdata.ClassCryptoSpot,
		Symbol: s.Symbol,
		Name:   name + "/" + s.QuoteAsset + " Spot",
		Status: refdata.StatusActive,
		Venues: []string{"binance"},
		Identifiers: map[string]string{
			"ticker":          s.BaseAsset,
			"exchange_symbol": s.Symbol,
		},
		Limits: lims,
		Meta:   meta,
	}
}

func listedOn(instID, venue string) refdata.Edge {
	return refdata.Edge{Subject: instID, Predicate: refdata.EdgeListedOn, Object: venue}
}

// buildSeed turns both exchangeInfo payloads into registry records. quotes
// is the set of accepted spot quote assets; e may be nil (bare exchangeInfo).
// skipped counts what was left out and why, for the summary line.
func buildSeed(spot spotExchangeInfo, fapi fapiExchangeInfo, quotes map[string]bool, e *enrichment) (seedFile, map[string]int) {
	seed := seedFile{}
	skipped := map[string]int{}
	for _, s := range fapi.Symbols {
		if s.ContractType != "PERPETUAL" {
			skipped["fapi contract "+s.ContractType]++
			continue
		}
		if s.Status != "TRADING" {
			skipped["fapi status "+s.Status]++
			continue
		}
		inst := perpInstrument(s, e)
		seed.Instruments = append(seed.Instruments, inst)
		seed.Edges = append(seed.Edges, listedOn(inst.ID, "venue:binance_usdt_m"))
	}
	for _, s := range spot.Symbols {
		if s.Status != "TRADING" || !s.IsSpotTradingAllowed {
			skipped["spot status "+s.Status]++
			continue
		}
		if !quotes[s.QuoteAsset] {
			skipped["spot quote "+s.QuoteAsset]++
			continue
		}
		inst := spotInstrument(s, e)
		seed.Instruments = append(seed.Instruments, inst)
		seed.Edges = append(seed.Edges, listedOn(inst.ID, "venue:binance"))
	}
	sortSeed(&seed)
	return seed, skipped
}

// ------------------------------------------------------- documents, merge

// generatedDocs are the supporting documents the generated registry needs:
// the crypto_spot class definition, one venue spec per venue, and a
// provenance note. The crypto_perp definition already lives in the seed and
// is preserved by the merge.
func generatedDocs(counts map[string]int, quotes []string, fetched time.Time, sources []string) []refdata.Document {
	spotDef := `# crypto_spot

A spot pair on a cryptocurrency exchange: ownership of the base asset,
settled immediately against the quote asset.

Registry conventions for this class:

- ` + "`id`" + ` shape: ` + "`crypto_spot:<venue>:<exchange_symbol>`" + ` in lowercase.
- ` + "`limits`" + ` keys are populated from the exchange's own filters:
  ` + "`tick_size`, `lot_size`, `min_qty`, `max_qty`, `min_notional`" + `.
- ` + "`meta.base_asset` / `meta.quote_asset`" + ` name the legs; ` + "`meta.asset_name`" + `
  is the display name from the exchange asset catalog, ` + "`meta.coin_link`" + ` links to
  the coin's page, and ` + "`meta.d24_*`" + ` keys are a 24-hour market snapshot taken
  at generation time.
`
	venuePerp := `# venue: binance_usdt_m

Binance USDⓈ-M perpetual futures (fapi). Every ` + "`crypto_perp:binance_usdt_m`" + `
record lists this venue and carries the contract's exchange-native trading
limits copied from ` + "`fapi/v1/exchangeInfo`" + ` at generation time: tick and lot
size, quantity bounds, maintenance margin, liquidation fee, trigger protect,
and — when API credentials are supplied to the generator — the leverage
bracket ladder (` + "`max_leverage`, `max_notional`" + `). Funding, mark price and
long/short sentiment live in ` + "`meta`" + ` with the same snapshot caveat.
`
	venueSpot := `# venue: binance

Binance spot (api). Every ` + "`crypto_spot:binance`" + ` record lists this
venue and carries the pair's exchange-native trading limits copied from
` + "`api/v3/exchangeInfo`" + ` at generation time.
`
	note := fmt.Sprintf(`# refseed generation note

Generated by `+"`cmd/refseed`"+` from Binance endpoints at %s.

- %d USDT-M perpetuals (TRADING)
- %d spot pairs (TRADING, quoted in %s)

Enrichments applied: %s.

Snapshot fields (`+"`meta.d24_*`, `meta.funding_rate`, `meta.mark_price`, `meta.*_ratio`"+`) describe the generation moment and go stale until the next
regeneration. Static fields (`+"`limits.*`, asset metadata, identifiers"+`) track
the exchange's published configuration.

Regenerate with: `+"`go run ./cmd/refseed -out ./cmd/refdata/seed.json`"+`
`,
		fetched.UTC().Format(time.RFC3339), counts["perp"], counts["spot"], strings.Join(quotes, ", "), strings.Join(sources, ", "))
	return []refdata.Document{
		{ID: "doc-class-crypto-spot-definition", Class: refdata.ClassCryptoSpot, Title: "crypto_spot class definition", Kind: "definition", Source: "cmd/refseed", Body: spotDef},
		{ID: "doc-venue-binance-usdt-m", Title: "venue: binance_usdt_m", Kind: "spec", Source: "cmd/refseed", Body: venuePerp},
		{ID: "doc-venue-binance-spot", Title: "venue: binance", Kind: "spec", Source: "cmd/refseed", Body: venueSpot},
		{ID: "doc-refseed-note", Title: "refseed generation note", Kind: "note", Source: "cmd/refseed", Body: note},
	}
}

// mergeSeed folds generated records into the existing seed file: generated
// instruments win on id collision, everything the exchange does not mention
// survives, documents are added unless a same-title+kind doc exists, and
// edges are unioned by triple. This keeps hand-curated registry content
// intact across regenerations.
func mergeSeed(seed, existing *seedFile, docs []refdata.Document) {
	haveInst := map[string]bool{}
	for _, in := range seed.Instruments {
		haveInst[in.ID] = true
	}
	for _, in := range existing.Instruments {
		if !haveInst[in.ID] {
			seed.Instruments = append(seed.Instruments, in)
		}
	}
	haveDoc := map[string]bool{}
	for _, d := range seed.Documents {
		haveDoc[d.Title+"\x00"+d.Kind] = true
	}
	for _, d := range existing.Documents {
		if !haveDoc[d.Title+"\x00"+d.Kind] {
			seed.Documents = append(seed.Documents, d)
			haveDoc[d.Title+"\x00"+d.Kind] = true
		}
	}
	for _, d := range docs {
		if !haveDoc[d.Title+"\x00"+d.Kind] {
			seed.Documents = append(seed.Documents, d)
			haveDoc[d.Title+"\x00"+d.Kind] = true
		}
	}
	haveEdge := map[string]bool{}
	for _, e := range seed.Edges {
		haveEdge[e.Subject+"\x00"+e.Predicate+"\x00"+e.Object] = true
	}
	for _, e := range existing.Edges {
		k := e.Subject + "\x00" + e.Predicate + "\x00" + e.Object
		if !haveEdge[k] {
			seed.Edges = append(seed.Edges, e)
			haveEdge[k] = true
		}
	}
	sortSeed(seed)
}

func sortSeed(seed *seedFile) {
	sort.Slice(seed.Instruments, func(i, j int) bool {
		a, b := seed.Instruments[i], seed.Instruments[j]
		if a.Class != b.Class {
			return a.Class < b.Class
		}
		return a.Symbol < b.Symbol
	})
	sort.Slice(seed.Edges, func(i, j int) bool {
		a, b := seed.Edges[i], seed.Edges[j]
		if a.Subject != b.Subject {
			return a.Subject < b.Subject
		}
		if a.Predicate != b.Predicate {
			return a.Predicate < b.Predicate
		}
		return a.Object < b.Object
	})
	sort.Slice(seed.Documents, func(i, j int) bool {
		return seed.Documents[i].ID < seed.Documents[j].ID
	})
}

// ------------------------------------------------------------------ fetch

var httpClient = &http.Client{Timeout: 30 * time.Second}

func fetchJSON(rawURL string, dst any) error {
	resp, err := httpClient.Get(rawURL) //nolint:gosec // URLs come from flags, defaults are Binance.
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("%s: HTTP %s", rawURL, resp.Status)
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, 64<<20))
	if err != nil {
		return err
	}
	return json.Unmarshal(body, dst)
}

// signedGet performs a signed (HMAC-SHA256) GET against a Binance futures
// endpoint, as required by USER_DATA reads like leverageBracket.
func signedGet(baseURL, path, key, secret string, dst any) error {
	q := url.Values{}
	q.Set("timestamp", strconv.FormatInt(time.Now().UnixMilli(), 10))
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(q.Encode()))
	q.Set("signature", hex.EncodeToString(mac.Sum(nil)))
	req, err := http.NewRequest(http.MethodGet, baseURL+path+"?"+q.Encode(), nil)
	if err != nil {
		return err
	}
	req.Header.Set("X-MBX-APIKEY", key)
	resp, err := httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 64<<20))
	if err != nil {
		return err
	}
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("%s: HTTP %s: %.200s", path, resp.Status, body)
	}
	return json.Unmarshal(body, dst)
}

// fetchProducts merges the website product catalog (supply, tags, display
// names) with the asset catalog (coin page links, logos) into one map keyed
// by base asset.
func fetchProducts() (map[string]assetInfo, error) {
	req, err := http.NewRequest(http.MethodGet, "https://www.binance.com/bapi/asset/v1/public/asset-service/product/get-products", nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "Mozilla/5.0")
	req.Header.Set("clienttype", "spot")
	resp, err := httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 64<<20))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("get-products: HTTP %s", resp.Status)
	}
	var pr productsResponse
	if err := json.Unmarshal(body, &pr); err != nil {
		return nil, err
	}
	out := map[string]assetInfo{}
	for _, p := range pr.Data {
		a := out[p.Base]
		if p.AssetName != "" {
			a.Name = p.AssetName
		}
		if p.QuoteName != "" {
			a.QuoteName = p.QuoteName
		}
		if p.Supply > 0 {
			a.Supply = strconv.FormatFloat(p.Supply, 'f', -1, 64)
		}
		if len(p.Tags) > 0 {
			a.Tags = strings.Join(p.Tags, ", ")
		}
		out[p.Base] = a
	}

	areq, err := http.NewRequest(http.MethodGet, "https://www.binance.com/bapi/asset/v2/public/asset/asset/get-all-asset", nil)
	if err != nil {
		return out, nil // names/supply still worth keeping
	}
	areq.Header.Set("User-Agent", "Mozilla/5.0")
	aresp, err := httpClient.Do(areq)
	if err != nil {
		return out, nil
	}
	defer aresp.Body.Close()
	abody, err := io.ReadAll(io.LimitReader(aresp.Body, 64<<20))
	if err != nil {
		return out, nil
	}
	var ar assetsResponse
	if err := json.Unmarshal(abody, &ar); err != nil {
		return out, nil
	}
	for _, d := range ar.Data {
		a := out[d.AssetCode]
		if a.Name == "" {
			a.Name = d.AssetDisplayName
		}
		a.Link = d.EnLink
		if d.FullLogoURL != "" {
			a.Logo = d.FullLogoURL
		} else {
			a.Logo = d.LogoURL
		}
		out[d.AssetCode] = a
	}
	return out, nil
}

// fetchRatios walks every perp symbol through the three public sentiment
// endpoints. Per-symbol failures are tolerated and counted.
func fetchRatios(ctx context.Context, symbols []string) (map[string]ratioInfo, int) {
	out := map[string]ratioInfo{}
	var mu sync.Mutex
	var failed int
	jobs := make(chan string)
	var wg sync.WaitGroup
	for w := 0; w < 4; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for sym := range jobs {
				r := ratioInfo{}
				var pts []ratioPoint
				if err := fetchJSON("https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol="+sym+"&period=1d&limit=1", &pts); err == nil && len(pts) > 0 {
					r.LongShort = pts[0].LongShortRatio
				}
				if err := fetchJSON("https://fapi.binance.com/futures/data/takerlongshortRatio?symbol="+sym+"&period=1d&limit=1", &pts); err == nil && len(pts) > 0 {
					r.TakerBuySell = pts[0].BuySellRatio
				}
				if err := fetchJSON("https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol="+sym+"&period=1d&limit=1", &pts); err == nil && len(pts) > 0 {
					r.GlobalLongStr = pts[0].LongShortRatio
				}
				mu.Lock()
				if r.LongShort == "" && r.TakerBuySell == "" && r.GlobalLongStr == "" {
					failed++
				} else {
					out[sym] = r
				}
				mu.Unlock()
			}
		}()
	}
feed:
	for _, sym := range symbols {
		select {
		case <-ctx.Done():
			break feed
		case jobs <- sym:
		}
	}
	close(jobs)
	wg.Wait()
	return out, failed
}

// ------------------------------------------------------------------- main

func parseQuotes(s string) map[string]bool {
	out := map[string]bool{}
	for _, q := range strings.Split(s, ",") {
		if q = strings.ToUpper(strings.TrimSpace(q)); q != "" {
			out[q] = true
		}
	}
	return out
}

func loadSeed(path string) (*seedFile, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return &seedFile{}, nil
		}
		return nil, err
	}
	var seed seedFile
	if err := json.Unmarshal(raw, &seed); err != nil {
		return nil, fmt.Errorf("existing seed %s: %w", path, err)
	}
	return &seed, nil
}

func writeSeed(path string, seed *seedFile) error {
	raw, err := json.MarshalIndent(seed, "", "  ")
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, raw, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func main() {
	out := flag.String("out", "./cmd/refdata/seed.json", "seed file to write (merged with existing content)")
	spotURL := flag.String("spot-url", "https://api.binance.com/api/v3/exchangeInfo", "spot exchangeInfo URL")
	futuresURL := flag.String("futures-url", "https://fapi.binance.com/fapi/v1/exchangeInfo", "USDT-M futures exchangeInfo URL")
	spotQuotes := flag.String("spot-quotes", "USDT,FDUSD,USDC,EUR", "comma-separated spot quote assets to include")
	fapiKey := flag.String("fapi-key", os.Getenv("BINANCE_FAPI_KEY"), "futures API key (unlocks leverage brackets; env BINANCE_FAPI_KEY)")
	fapiSecret := flag.String("fapi-secret", os.Getenv("BINANCE_FAPI_SECRET"), "futures API secret (env BINANCE_FAPI_SECRET)")
	sentiment := flag.Bool("sentiment", true, "fetch per-symbol long/short sentiment ratios (perp only, ~3 requests per symbol)")
	flag.Parse()

	var spot spotExchangeInfo
	if err := fetchJSON(*spotURL, &spot); err != nil {
		fmt.Fprintf(os.Stderr, "refseed: spot exchangeInfo: %v\n", err)
		os.Exit(1)
	}
	var fapi fapiExchangeInfo
	if err := fetchJSON(*futuresURL, &fapi); err != nil {
		fmt.Fprintf(os.Stderr, "refseed: futures exchangeInfo: %v\n", err)
		os.Exit(1)
	}

	e := &enrichment{
		Assets:   map[string]assetInfo{},
		Perp24:   map[string]ticker24{},
		Spot24:   map[string]ticker24{},
		Premium:  map[string]premium{},
		Brackets: map[string]bracketInfo{},
		Ratios:   map[string]ratioInfo{},
	}
	sources := []string{"exchangeInfo (spot + USDT-M)"}
	warn := func(src string, err error) {
		fmt.Fprintf(os.Stderr, "refseed: warning: %s unavailable: %v (continuing without it)\n", src, err)
		sources = append(sources, src+" (FAILED)")
	}

	assets, err := fetchProducts()
	if err != nil {
		warn("asset catalog", err)
	} else {
		e.Assets = assets
		sources = append(sources, "asset catalog (names, supply, tags, links)")
	}

	var perp24, spot24 []ticker24
	if err := fetchJSON("https://fapi.binance.com/fapi/v1/ticker/24hr", &perp24); err != nil {
		warn("futures 24h ticker", err)
	} else {
		for _, t := range perp24 {
			e.Perp24[t.Symbol] = t
		}
		sources = append(sources, "futures 24h ticker")
	}
	if err := fetchJSON("https://api.binance.com/api/v3/ticker/24hr", &spot24); err != nil {
		warn("spot 24h ticker", err)
	} else {
		for _, t := range spot24 {
			e.Spot24[t.Symbol] = t
		}
		sources = append(sources, "spot 24h ticker")
	}

	var premiums []premium
	if err := fetchJSON("https://fapi.binance.com/fapi/v1/premiumIndex", &premiums); err != nil {
		warn("funding / premium index", err)
	} else {
		for _, p := range premiums {
			e.Premium[p.Symbol] = p
		}
		sources = append(sources, "funding / mark price")
	}

	perpSymbols := []string{}
	for _, s := range fapi.Symbols {
		if s.ContractType == "PERPETUAL" && s.Status == "TRADING" {
			perpSymbols = append(perpSymbols, s.Symbol)
		}
	}

	if *fapiKey != "" && *fapiSecret != "" {
		var brackets []struct {
			Symbol   string `json:"symbol"`
			Brackets []struct {
				InitialLeverage float64 `json:"initialLeverage"`
				NotionalCap     float64 `json:"notionalCap"`
			} `json:"brackets"`
		}
		if err := signedGet("https://fapi.binance.com", "/fapi/v1/leverageBracket", *fapiKey, *fapiSecret, &brackets); err != nil {
			warn("leverage brackets", err)
		} else {
			for _, b := range brackets {
				if len(b.Brackets) == 0 {
					continue
				}
				top := b.Brackets[len(b.Brackets)-1]
				e.Brackets[b.Symbol] = bracketInfo{
					MaxLeverage: b.Brackets[0].InitialLeverage,
					MaxNotional: top.NotionalCap,
				}
			}
			sources = append(sources, "leverage brackets (max leverage / max notional)")
		}
	} else {
		fmt.Fprintf(os.Stderr, "refseed: no -fapi-key/-fapi-secret: leverage brackets skipped (limits.max_leverage / limits.max_notional left empty)\n")
	}

	if *sentiment {
		ratios, failed := fetchRatios(context.Background(), perpSymbols)
		e.Ratios = ratios
		sources = append(sources, fmt.Sprintf("long/short sentiment (%d symbols, %d failed)", len(ratios), failed))
	}

	quotes := parseQuotes(*spotQuotes)
	seed, skipped := buildSeed(spot, fapi, quotes, e)
	counts := map[string]int{}
	for _, in := range seed.Instruments {
		switch in.Class {
		case refdata.ClassCryptoPerp:
			counts["perp"]++
		case refdata.ClassCryptoSpot:
			counts["spot"]++
		}
	}

	existing, err := loadSeed(*out)
	if err != nil {
		fmt.Fprintf(os.Stderr, "refseed: %v\n", err)
		os.Exit(1)
	}
	kept := 0
	haveGen := map[string]bool{}
	for _, g := range seed.Instruments {
		haveGen[g.ID] = true
	}
	for _, in := range existing.Instruments {
		if !haveGen[in.ID] {
			kept++
		}
	}
	quoteList := []string{}
	for q := range quotes {
		quoteList = append(quoteList, q)
	}
	sort.Strings(quoteList)
	mergeSeed(&seed, existing, generatedDocs(counts, quoteList, time.Now(), sources))

	if err := os.MkdirAll(filepath.Dir(*out), 0o755); err != nil {
		fmt.Fprintf(os.Stderr, "refseed: %v\n", err)
		os.Exit(1)
	}
	if err := writeSeed(*out, &seed); err != nil {
		fmt.Fprintf(os.Stderr, "refseed: write %s: %v\n", *out, err)
		os.Exit(1)
	}
	fmt.Printf("refseed: %d instruments (%d perp, %d spot), %d documents, %d edges -> %s\n",
		len(seed.Instruments), counts["perp"], counts["spot"], len(seed.Documents), len(seed.Edges), *out)
	fmt.Printf("refseed: kept %d hand-curated instruments not mentioned by Binance\n", kept)
	fmt.Printf("refseed: enrichments: %s\n", strings.Join(sources, "; "))
	if len(skipped) > 0 {
		reasons := []string{}
		for r := range skipped {
			reasons = append(reasons, r)
		}
		sort.Strings(reasons)
		for _, r := range reasons {
			fmt.Printf("refseed: skipped %4d (%s)\n", skipped[r], r)
		}
	}
}
