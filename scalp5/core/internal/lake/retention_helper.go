package lake

import (
	"bufio"
	"compress/gzip"
	"encoding/json"
	"io"
	"os"
	"strings"
)

func marshalRecord(r Record) ([]byte, error) { return json.Marshal(r) }

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
