// lakeindex freezes an existing journal file into the segment directory: one scan,
// a verified sidecar, optional gzip, and a sampled A-B check.
//
//	./bin/lakeindex -dir ../state/lake3 -segment ../state/lake3/seg-0000.jsonl -verify 200
package main

import (
	"flag"
	"fmt"
	"os"

	"cryptalyst/core/internal/lake"
)

func main() {
	dir := flag.String("dir", "", "segment directory (sidecars are written beside the segment)")
	segment := flag.String("segment", "", "segment file to index")
	verify := flag.Int("verify", 200, "sampled windows to check against a direct scan")
	checkpointMB := flag.Int64("checkpoint-mb", 256, "checkpoint interval in MB")
	minFreeGB := flag.Int64("min-free-gb", 100, "refuse to start below this much free space")
	flag.Parse()

	if *dir == "" || *segment == "" {
		fmt.Fprintln(os.Stderr, "usage: lakeindex -dir DIR -segment FILE [-verify N]")
		os.Exit(2)
	}

	// 100 GB free-space abort, as ruled
	if free, err := freeBytes(*dir); err == nil && free < uint64(*minFreeGB)<<30 {
		fmt.Fprintf(os.Stderr, "refusing to index: %d GB free, need at least %d GB\n", free>>30, *minFreeGB)
		os.Exit(1)
	}

	fmt.Printf("indexing %s (checkpoint every %d MB)\n", *segment, *checkpointMB)
	st, err := lake.BuildIndexForSegment(*segment, *checkpointMB<<20)
	if err != nil {
		fmt.Fprintln(os.Stderr, "index:", err)
		os.Exit(1)
	}
	fmt.Printf("indexed: %d records, %d bytes, %d topics\n", st.Records, st.Bytes, len(st.Topics))
	for _, t := range st.Topics {
		fmt.Printf("  %-20s %8d  %d..%d\n", t.Topic, t.Records, t.From, t.To)
	}

	if *verify > 0 {
		if err := lake.SampleVerify(*dir, *segment, *verify); err != nil {
			fmt.Fprintln(os.Stderr, "verify:", err)
			os.Exit(1)
		}
		fmt.Printf("verified: %d sampled windows agree between plan and direct scan\n", *verify)
	}
}
