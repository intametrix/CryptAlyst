// Package web embeds the built React dashboard so the server ships as a single
// binary with no runtime asset path to get wrong.
package web

import (
	"embed"
	"io/fs"
)

// dist holds the Vite build output. The `all:` prefix includes files that
// would otherwise be skipped (dotfiles and underscored names).
//
//go:embed all:dist
var dist embed.FS

// Dist returns the dashboard filesystem rooted at the build output directory.
// It returns nil if the build output is missing, and the API then serves a
// clear "dashboard not built" error rather than panicking.
func Dist() fs.FS {
	sub, err := fs.Sub(dist, "dist")
	if err != nil {
		return nil
	}
	if _, err := fs.Stat(sub, "index.html"); err != nil {
		return nil
	}
	return sub
}
