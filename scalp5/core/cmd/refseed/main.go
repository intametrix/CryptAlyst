// Command refseed generates the refdata seed file from Binance exchangeInfo.
//
// Two public endpoints are read: spot (api.binance.com/api/v3/exchangeInfo)
// and USDⓈ-M futures (fapi.binance.com/fapi/v1/exchangeInfo). Every TRADING
// USDT-M perpetual becomes a crypto_perp:binance_usdt_m record; every TRADING
// spot pair whose quote asset is in -spot-quotes (default
// USDT,FDUSD,USDC,EUR) becomes a crypto_spot:binance record. Records carry
// the exchange's own trading limits (tick size, lot size, min/max quantity,
// min notional, precision) plus base/quote/margin asset and contract metadata.
//
// The existing seed file is merged, never replaced: hand-curated records that
// Binance does not mention (e.g. testing-marked contracts) survive, and
// documents/edges are unioned so nothing already in the registry is lost.
//
// Regenerate with:
//
//	go run ./cmd/refseed -out ./cmd/refdata/seed.json
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"cryptalyst/core/internal/refdata"
)

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

func perpInstrument(s fapiSymbol) refdata.Instrument {
	lims := map[string]float64{}
	fnum(lims, "tick_size", filterOf(s.Filters, "PRICE_FILTER"), "tickSize")
	lot := filterOf(s.Filters, "LOT_SIZE")
	fnum(lims, "lot_size", lot, "stepSize")
	fnum(lims, "min_qty", lot, "minQty")
	fnum(lims, "max_qty", lot, "maxQty")
	fnum(lims, "min_notional", filterOf(s.Filters, "MIN_NOTIONAL"), "minNotional")
	lims["price_precision"] = float64(s.PricePrecision)
	lims["quantity_precision"] = float64(s.QuantityPrecision)
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
	return refdata.Instrument{
		ID:     "crypto_perp:binance_usdt_m:" + strings.ToLower(s.Symbol),
		Class:  refdata.ClassCryptoPerp,
		Symbol: s.Symbol,
		Name:   s.BaseAsset + " USDT-M Perpetual",
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

func spotInstrument(s spotSymbol) refdata.Instrument {
	lims := map[string]float64{}
	fnum(lims, "tick_size", filterOf(s.Filters, "PRICE_FILTER"), "tickSize")
	lot := filterOf(s.Filters, "LOT_SIZE")
	fnum(lims, "lot_size", lot, "stepSize")
	fnum(lims, "min_qty", lot, "minQty")
	fnum(lims, "max_qty", lot, "maxQty")
	// Newer spot listings use NOTIONAL, older ones MIN_NOTIONAL.
	fnum(lims, "min_notional", filterOf(s.Filters, "NOTIONAL"), "minNotional")
	fnum(lims, "min_notional", filterOf(s.Filters, "MIN_NOTIONAL"), "minNotional")
	return refdata.Instrument{
		ID:     "crypto_spot:binance:" + strings.ToLower(s.Symbol),
		Class:  refdata.ClassCryptoSpot,
		Symbol: s.Symbol,
		Name:   s.BaseAsset + "/" + s.QuoteAsset + " Spot",
		Status: refdata.StatusActive,
		Venues: []string{"binance"},
		Identifiers: map[string]string{
			"ticker":          s.BaseAsset,
			"exchange_symbol": s.Symbol,
		},
		Limits: lims,
		Meta: map[string]string{
			"base_asset":  s.BaseAsset,
			"quote_asset": s.QuoteAsset,
			"source":      "binance:api/v3/exchangeInfo",
		},
	}
}

func listedOn(instID, venue string) refdata.Edge {
	return refdata.Edge{Subject: instID, Predicate: refdata.EdgeListedOn, Object: venue}
}

// buildSeed turns both exchangeInfo payloads into registry records. quotes
// is the set of accepted spot quote assets. skipped counts what was left
// out and why, for the summary line.
func buildSeed(spot spotExchangeInfo, fapi fapiExchangeInfo, quotes map[string]bool) (seedFile, map[string]int) {
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
		inst := perpInstrument(s)
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
		inst := spotInstrument(s)
		seed.Instruments = append(seed.Instruments, inst)
		seed.Edges = append(seed.Edges, listedOn(inst.ID, "venue:binance"))
	}
	sortSeed(&seed)
	return seed, skipped
}

// generatedDocs are the supporting documents the generated registry needs:
// the crypto_spot class definition, one venue spec per venue, and a
// provenance note. The crypto_perp definition already lives in the seed and
// is preserved by the merge.
func generatedDocs(counts map[string]int, quotes []string, fetched time.Time) []refdata.Document {
	spotDef := `# crypto_spot

A spot pair on a cryptocurrency exchange: ownership of the base asset,
settled immediately against the quote asset.

Registry conventions for this class:

- ` + "`id`" + ` shape: ` + "`crypto_spot:<venue>:<exchange_symbol>`" + ` in lowercase.
- ` + "`limits`" + ` keys are populated from the exchange's own filters:
  ` + "`tick_size`, `lot_size`, `min_qty`, `max_qty`, `min_notional`" + `.
- ` + "`meta.base_asset` / `meta.quote_asset`" + ` name the legs; ` + "`meta.source`" + `
  names the exchangeInfo endpoint the record was generated from.
`
	venuePerp := `# venue: binance_usdt_m

Binance USDⓈ-M perpetual futures (fapi). Every ` + "`crypto_perp:binance_usdt_m`" + `
record lists this venue and carries the contract's exchange-native trading
limits copied from ` + "`fapi/v1/exchangeInfo`" + ` at generation time.
`
	venueSpot := `# venue: binance

Binance spot (api). Every ` + "`crypto_spot:binance`" + ` record lists this
venue and carries the pair's exchange-native trading limits copied from
` + "`api/v3/exchangeInfo`" + ` at generation time.
`
	note := fmt.Sprintf(`# refseed generation note

Generated by `+"`cmd/refseed`"+` from Binance exchangeInfo at %s.

- %d USDT-M perpetuals (TRADING)
- %d spot pairs (TRADING, quoted in %s)

Regenerate with: `+"`go run ./cmd/refseed -out ./cmd/refdata/seed.json`"+`
`,
		fetched.UTC().Format(time.RFC3339), counts["perp"], counts["spot"], strings.Join(quotes, ", "))
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

func fetchJSON(url string, dst any) error {
	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Get(url) //nolint:gosec // URL comes from a flag, default is Binance.
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("%s: HTTP %s", url, resp.Status)
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, 32<<20))
	if err != nil {
		return err
	}
	return json.Unmarshal(body, dst)
}

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

	quotes := parseQuotes(*spotQuotes)
	seed, skipped := buildSeed(spot, fapi, quotes)
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
	for _, in := range existing.Instruments {
		found := false
		for _, g := range seed.Instruments {
			if g.ID == in.ID {
				found = true
				break
			}
		}
		if !found {
			kept++
		}
	}
	quoteList := []string{}
	for q := range quotes {
		quoteList = append(quoteList, q)
	}
	sort.Strings(quoteList)
	mergeSeed(&seed, existing, generatedDocs(counts, quoteList, time.Now()))

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
