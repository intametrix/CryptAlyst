import { useCallback, useEffect, useRef, useState } from 'react';
import { getJSON } from '../data/client';
import { usePoll } from './usePoll';

export interface Resource<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  /** When the last successful read landed, for a freshness label. */
  updatedMs: number;
  reload: () => void;
}

/**
 * Load, poll, abort and fail — once.
 *
 * Every panel used to hand-roll this and they disagreed about what a failed
 * refresh looks like. Here a failure is always visible and never silently keeps
 * the last value on screen without saying so.
 */
export function useResource<T>(path: string | null, options: { pollMs?: number; enabled?: boolean } = {}): Resource<T> {
  const { pollMs = 0, enabled = true } = options;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [updatedMs, setUpdatedMs] = useState(0);
  const [nonce, setNonce] = useState(0);
  const abort = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    if (path === null || !enabled) return;
    abort.current?.abort();
    const ctl = new AbortController();
    abort.current = ctl;
    setLoading(true);
    try {
      const body = await getJSON<T>(path, ctl.signal);
      if (ctl.signal.aborted) return;
      setData(body);
      setError(null);
      setUpdatedMs(Date.now());
    } catch (e) {
      if (ctl.signal.aborted) return;
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (!ctl.signal.aborted) setLoading(false);
    }
  }, [path, enabled]);

  useEffect(() => {
    void load();
    return () => abort.current?.abort();
  }, [load, nonce]);

  usePoll(load, pollMs, { enabled: enabled && pollMs > 0 });

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data, error, loading, updatedMs, reload };
}
