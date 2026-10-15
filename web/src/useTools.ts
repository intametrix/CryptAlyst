import { useCallback, useEffect, useState } from 'react';
import { fetchJson } from './api';
import { CANON_FALLBACK, applyCanonToCss, parseCanon, type ColorCanon } from './colors';
import { LOCAL_TOOLS, resolveTooltip, type TooltipSpec } from './glossary';
import type { ToolEntry, ToolsMsg } from './types';

/**
 * Fetch `/api/colors` **once** and publish it.
 *
 * The palette becomes CSS custom properties, so both the dashboard chrome and the
 * chart library read one set of values. A failure is silent: the documented
 * fallbacks are already in the stylesheet, so the UI renders identically except
 * for the exact hues.
 */
export function useColorCanon(): ColorCanon {
  const [canon, setCanon] = useState<ColorCanon>(CANON_FALLBACK);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    void (async () => {
      const body = await fetchJson<unknown>('/api/colors', controller.signal);
      if (cancelled) return;
      const parsed = parseCanon(body);
      if (parsed === null) return;
      setCanon(parsed);
      applyCanonToCss(parsed);
    })();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, []);

  // The fallback is applied first so the tokens exist even before the fetch.
  useEffect(() => {
    applyCanonToCss(canon);
  }, [canon]);

  return canon;
}

export interface ToolsState {
  /** Raw `/api/tools` payload, or `null` while it is absent (offline/404). */
  tools: ToolsMsg | null;
  loading: boolean;
  /** True when the endpoint could not be used — the local copy is carrying the UI. */
  fallback: boolean;
  /** Re-try the endpoint (used after a reset or by a manual retry button). */
  reload: () => void;
}

/**
 * Fetch the glossary **once** and cache it in state.
 *
 * API.md: "The UI must fall back gracefully if a key is missing ... and render
 * the raw key name rather than crashing." The backend may also 404 this route
 * entirely while it is being built, so a failure here must be silent: it flips
 * `fallback` to true and every tooltip then resolves from `LOCAL_TOOLS`.
 *
 * Nothing is retried automatically — tooltips are not worth a poll loop — but
 * `reload()` lets the UI try again on demand.
 */
export function useTools(): ToolsState {
  const [tools, setTools] = useState<ToolsMsg | null>(null);
  const [loading, setLoading] = useState(true);
  const [fallback, setFallback] = useState(false);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    setLoading(true);

    void (async () => {
      const body = await fetchJson<ToolsMsg>('/api/tools', controller.signal);
      if (cancelled) return;
      // Accept the payload only if it actually carries a glossary object.
      const usable =
        body !== null && (typeof body.indicators === 'object' || typeof body.metrics === 'object');
      setTools(usable ? body : null);
      setFallback(!usable);
      setLoading(false);
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  return { tools, loading, fallback, reload };
}

/** How many entries the glossary can resolve locally, for the pill's title. */
export function localToolCount(): number {
  return Object.keys(LOCAL_TOOLS).length;
}

/** Total entries available in the loaded glossary (for the header pill). */
export function toolCount(tools: ToolsMsg | null): number {
  if (tools === null) return 0;
  const indicators = tools.indicators !== null && tools.indicators !== undefined ? Object.keys(tools.indicators).length : 0;
  const metrics = tools.metrics !== null && tools.metrics !== undefined ? Object.keys(tools.metrics).length : 0;
  return indicators + metrics;
}

/** Resolve one entry, for callers that want the spec rather than a component. */
export function resolveOne(
  keys: readonly string[],
  tools: ToolsMsg | null,
  fallbackTitle?: string,
  label?: string | null,
): TooltipSpec {
  return resolveTooltip(keys, tools, fallbackTitle, label);
}

/** Look up a single key without any fallback chain (used by tests/summaries). */
export function entryFor(tools: ToolsMsg | null, key: string): ToolEntry | null {
  return tools?.indicators?.[key] ?? tools?.metrics?.[key] ?? LOCAL_TOOLS[key] ?? null;
}
