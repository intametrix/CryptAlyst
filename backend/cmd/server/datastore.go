package main

import (
	"io"
	"os"
	"path/filepath"
)

// Durable-store path resolution.
//
// The four stores (alerts, ledger, turtle sessions, calibration) used to
// default to bare file names, which made their location a function of the
// launch directory: start the binary from backend/ and the data lands in
// backend/, start it from the repo root and it lands in the root, silently
// splitting one logical dataset across directories. -data-dir anchors them:
// every store left at its flag default resolves under the data directory,
// so data always lands in one known place no matter where the binary is
// launched from. An EXPLICIT store flag still wins -- an operator pointing a
// store somewhere else means it.

// storeSpec describes one durable store for path resolution.
type storeSpec struct {
	name       string // log label
	flagName   string // flag as spelled on the command line
	defaultVal string // flag default (bare file name)
	current    string // value after flag parsing
}

// resolveStorePaths reconciles -data-dir with explicitly overridden store
// flags. Explicit flags always win, including an explicit empty value (which
// disables that store and must stay empty). Stores left at their default are
// anchored under dataDir; an empty dataDir preserves the historical
// launch-directory behaviour.
func resolveStorePaths(dataDir string, specs []storeSpec, explicit map[string]bool) map[string]string {
	out := make(map[string]string, len(specs))
	for _, s := range specs {
		switch {
		case explicit[s.flagName]:
			out[s.name] = s.current
		case dataDir == "":
			out[s.name] = s.current
		default:
			out[s.name] = filepath.Join(dataDir, filepath.Base(s.defaultVal))
		}
	}
	return out
}

// importLegacyStore copies a pre-data-dir store file from the launch directory
// into its resolved location, once. Existing data is never overwritten and the
// original is left in place: copying, not moving, because the no-data-is-
// forgotten rule beats tidiness. reports whether a copy happened.
func importLegacyStore(resolved, legacyCWD string) (bool, error) {
	if resolved == "" || legacyCWD == "" || resolved == legacyCWD {
		return false, nil
	}
	if _, err := os.Stat(resolved); err == nil {
		return false, nil // the resolved store already exists: never touch it
	}
	src, err := os.Open(legacyCWD)
	if err != nil {
		return false, nil // no legacy file here: nothing to import
	}
	defer src.Close()
	if err := os.MkdirAll(filepath.Dir(resolved), 0o755); err != nil {
		return false, err
	}
	dst, err := os.OpenFile(resolved, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o644)
	if err != nil {
		return false, err
	}
	defer dst.Close()
	_, err = io.Copy(dst, src)
	return err == nil, err
}
