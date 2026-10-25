package api

import "net/http"

// GET /api/graph/nodes -> the strategy-graph palette: every node's id, title,
// kind, and typed input/output ports, so the designer can render a catalog
// without knowing any particular node.
func (s *Server) handleGraphNodes(w http.ResponseWriter, r *http.Request) {
	if s.deps.Graph == nil {
		writeJSON(w, http.StatusOK, map[string]any{"nodes": []any{}})
		return
	}
	out := make([]any, 0)
	for _, n := range s.deps.Graph.List() {
		out = append(out, map[string]any{
			"id":      n.ID(),
			"title":   n.Title(),
			"kind":    n.Kind(),
			"inputs":  n.Inputs(),
			"outputs": n.Outputs(),
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{"nodes": out})
}
