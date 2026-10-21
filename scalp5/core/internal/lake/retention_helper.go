package lake

import (
	"bufio"
	"compress/gzip"
	"encoding/json"
	"io"
	"os"
	"strings"
)

// marshalRecord strips the duplicate payload, exactly as the plain journal does:
// sealed segments should not double every line either.
func marshalRecord(r Record) ([]byte, error) {
	stripped, err := stripPayload(r)
	if err != nil {
		return nil, err
	}
	return json.Marshal(stripped)
}

func copyAll(dst io.Writer, src io.Reader) (int64, error) { return io.Copy(dst, src) }

func replayFile(path string, fn func(Record) error) (int, error) {
	f, err := os.Open(path)
	if err != nil {
		return 0, err
	}
	defer f.Close()
	var rdr io.Reader = f
	if strings.HasSuffix(path, ".gz") {
		gz, err := gzip.NewReader(f)
		if err != nil {
			return 0, err
		}
		defer gz.Close()
		rdr = gz
	}
	sc := bufio.NewScanner(rdr)
	sc.Buffer(make([]byte, 1<<20), 16<<20)
	n := 0
	for sc.Scan() {
		var r Record
		if err := json.Unmarshal(sc.Bytes(), &r); err != nil {
			continue
		}
		if err := fn(r); err != nil {
			return n, err
		}
		n++
	}
	return n, sc.Err()
}
