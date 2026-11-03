package api

import (
	"net/http"
	"sort"
)

// The forecast model catalogue, served rather than duplicated.
//
// Distinct from /api/models, which reports detector health.
//
// The UI kept its own list of model names, so it could offer a model the platform
// cannot compute (timestral without an agent) and would silently miss any new one
// added to the registry. One endpoint describes what exists.

type modelEntry struct {
	Name     string `json:"name"`
	Family   string `json:"family"`
	Volume   bool   `json:"volume"`
	Agent    bool   `json:"agent_only"`
	Platform bool   `json:"platform"`
}

func modelCatalog() []modelEntry {
	reg := getRegistry()
	out := make([]modelEntry, 0, len(reg))
	for name, spec := range reg {
		out = append(out, modelEntry{
			Name:     name,
			Family:   spec.Family,
			Volume:   spec.Volume,
			Agent:    spec.AgentOnly,
			Platform: spec.Path != nil,
		})
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Family != out[j].Family {
			return out[i].Family < out[j].Family
		}
		return out[i].Name < out[j].Name
	})
	return out
}

func (s *Server) handleForecastModels(w http.ResponseWriter, r *http.Request) {
	ok(w, map[string]any{"models": modelCatalog()})
}
