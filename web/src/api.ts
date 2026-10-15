import { useCallback, useEffect, useRef, useState } from 'react';
import type {
  AlertMsg,
  Config,
  ConnectionState,
  FlowMsg,
  HeatMsg,
  ScreenMsg,
  TickerMsg,
  StatusMsg,
  StreamMsg,
  SurveyMsg,
} from './types';
import { alertKey } from './types';

/** Rolling alert buffer size required by API.md ("max 100 kept in memory"). */
export const MAX_ALERTS = 100;

/** Upper bound on remembered "already flashed" ids, so the set cannot grow forever. */
const MAX_FLASH_IDS = 400;

/** Named SSE event types the backend may use instead of the default `message`. */
const STREAM_EVENT_TYPES = [
  'hello',
  'status',
  'flow',
  'alert',
  'screen',
  'survey',
  'heat',
  'ticker',
] as const;

/** Kind/severity tallies derived from the in-memory alert buffer. */
export interface AlertStats {
  total: number;
  pumps: number;
  dumps: number;
  reversals: number;
  critical: number;
}

export const EMPTY_ALERT_STATS: AlertStats = {
  total: 0,
  pumps: 0,
  dumps: 0,
  reversals: 0,
  critical: 0,
};

export function countAlerts(list: readonly AlertMsg[]): AlertStats {
  const stats: AlertStats = { ...EMPTY_ALERT_STATS, total: list.length };
  for (const a of list) {
    if (a.kind === 'PUMP') stats.pumps += 1;
    else if (a.kind === 'DUMP') stats.dumps += 1;
    else if (a.kind === 'REVERSAL_DUMP' || a.kind === 'REVERSAL_PUMP') stats.reversals += 1;
    if (a.severity === 'CRITICAL') stats.critical += 1;
  }
  return stats;
}

/** Everything the dashboard needs from the live stream. */
export interface EventStream {
  /** Reflected from EventSource `onopen` / `onerror`. */
  connection: ConnectionState;
  /** `hello.server_time_ms`, for clock-skew tooltips. */
  serverTimeMs: number | null;
  /** Local receipt time of the most recent parsed message. */
  lastEventMs: number | null;
  config: Config | null;
  status: StatusMsg | null;
  flow: FlowMsg | null;
  screen: ScreenMsg | null;
  survey: SurveyMsg | null;
  /** Newest first, capped at MAX_ALERTS. Frozen while `paused`. */
  alerts: AlertMsg[];
  /** Tallies over the whole buffer (stays live even while paused). */
  alertStats: AlertStats;
  /** `event_id`s that arrived live — used to trigger the one-shot flash. */
  flashIds: ReadonlySet<string>;
  paused: boolean;
  /** Alerts that arrived while paused and are not yet displayed. */
  pendingAlerts: number;
  setPaused: (paused: boolean) => void;
  /** `POST /api/screen/refresh` — the result arrives over SSE. */
  refreshScreen: () => void;
  /** `POST /api/survey/refresh` — the result arrives over SSE. */
  refreshSurvey: () => void;
  /** `DELETE /api/alerts` plus a local buffer reset. */
  clearAlerts: () => void;
}

function parseStreamMsg(raw: unknown): StreamMsg | null {
  if (typeof raw !== 'string' || raw === '') return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return null;
    const { type } = parsed as { type?: unknown };
    if (typeof type !== 'string') return null;
    return parsed as StreamMsg;
  } catch {
    return null;
  }
}

async function getJson<T>(url: string): Promise<T | null> {
  try {
    const res = await fetch(url, { headers: { accept: 'application/json' } });
    if (!res.ok) return null;
    // A backend that has not implemented an endpoint yet may answer with the
    // SPA's index.html (200 + text/html) rather than a 404. Parsing that as
    // JSON would throw, so only accept a JSON content type.
    const contentType = res.headers.get('content-type') ?? '';
    if (!contentType.toLowerCase().includes('json')) return null;
    const body: unknown = await res.json();
    if (typeof body !== 'object' || body === null) return null;
    return body as T;
  } catch {
    return null;
  }
}

/**
 * Shared guarded GET: resolves to the parsed body, or `null` for any failure —
 * HTTP error, network error, HTML instead of JSON, or a non-object body.
 *
 * Exported so the charting hooks can fetch `/api/klines` without duplicating
 * the content-type guard above.
 */
export async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T | null> {
  try {
    const res = await fetch(url, signal === undefined ? { headers: { accept: 'application/json' } } : { headers: { accept: 'application/json' }, signal });
    if (!res.ok) return null;
    const contentType = res.headers.get('content-type') ?? '';
    if (!contentType.toLowerCase().includes('json')) return null;
    const body: unknown = await res.json();
    if (typeof body !== 'object' || body === null) return null;
    return body as T;
  } catch {
    return null;
  }
}

/** Fetch status that distinguishes "still loading" from "failed". */
export interface FetchState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  /** Local wall-clock ms of the last successful load. */
  atMs: number | null;
}

/**
 * One-shot JSON GET with an explicit loading/error state, so a failing endpoint
 * can surface an error without blanking the rest of the UI.
 *
 * `pollMs` re-fetches on an interval, but only while the tab is visible.
 */
export function useJsonEndpoint<T>(url: string | null, pollMs?: number): FetchState<T> {
  const [state, setState] = useState<FetchState<T>>({ data: null, loading: url !== null, error: null, atMs: null });

  useEffect(() => {
    if (url === null) {
      setState({ data: null, loading: false, error: null, atMs: null });
      return;
    }
    let cancelled = false;
    const controller = new AbortController();

    const run = async (): Promise<void> => {
      const body = await fetchJson<T>(url, controller.signal);
      if (cancelled) return;
      if (body === null) {
        setState((prev) => ({ ...prev, loading: false, error: `request to ${url} failed` }));
        return;
      }
      setState({ data: body, loading: false, error: null, atMs: Date.now() });
    };

    setState((prev) => ({ ...prev, loading: true }));
    void run();

    let timer: number | null = null;
    if (pollMs !== undefined && pollMs > 0) {
      timer = window.setInterval(() => {
        if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return;
        void run();
      }, pollMs);
    }

    return () => {
      cancelled = true;
      controller.abort();
      if (timer !== null) window.clearInterval(timer);
    };
  }, [url, pollMs]);

  return state;
}

async function send(url: string, method: 'POST' | 'DELETE'): Promise<boolean> {
  try {
    const res = await fetch(url, { method });
    return res.ok;
  } catch {
    return false;
  }
}

/**
 * One `EventSource('/api/events')` for the whole app.
 *
 * Messages are JSON objects discriminated by `type` (API.md); they may arrive
 * as default `message` events or as named events, so both are wired to the same
 * handler. The connection pill follows `onopen`/`onerror`; reconnection itself
 * is left to `EventSource`, which retries on its own.
 */
export interface EventStreamOptions {
  /**
   * Called for every `heat` message. Heat is deliberately *not* React state: it
   * arrives for the whole universe every ~2s, and holding it in state here would
   * re-render the entire dashboard on each tick. The store subscribes per symbol.
   */
  onHeat?: (payload: HeatMsg) => void;
  /**
   * Called for every `ticker` frame (~4 Hz, 25 symbols per frame).
   *
   * Like heat, this never touches React state: at 4 Hz a state write per frame
   * would re-render the whole dashboard. The charted symbol's row is read from
   * the callback and applied straight to the series.
   */
  onTicker?: (payload: TickerMsg) => void;
}

export function useEventStream(options: EventStreamOptions = {}): EventStream {
  const [connection, setConnection] = useState<ConnectionState>('connecting');
  const [serverTimeMs, setServerTimeMs] = useState<number | null>(null);
  const [lastEventMs, setLastEventMs] = useState<number | null>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [status, setStatus] = useState<StatusMsg | null>(null);
  const [flow, setFlow] = useState<FlowMsg | null>(null);
  const [screen, setScreen] = useState<ScreenMsg | null>(null);
  const [survey, setSurvey] = useState<SurveyMsg | null>(null);
  const [alerts, setAlerts] = useState<AlertMsg[]>([]);
  const [alertStats, setAlertStats] = useState<AlertStats>(EMPTY_ALERT_STATS);
  const [paused, setPausedState] = useState(false);
  const [pendingAlerts, setPendingAlerts] = useState(0);

  const bufferRef = useRef<AlertMsg[]>([]);
  const flashRef = useRef<Set<string>>(new Set());
  const pausedRef = useRef(false);
  const heatRef = useRef<EventStreamOptions['onHeat']>(options.onHeat);
  heatRef.current = options.onHeat;
  const tickerRef = useRef<EventStreamOptions['onTicker']>(options.onTicker);
  tickerRef.current = options.onTicker;

  const ingestAlert = useCallback((alert: AlertMsg) => {
    const next = [alert, ...bufferRef.current];
    if (next.length > MAX_ALERTS) next.length = MAX_ALERTS;
    bufferRef.current = next;

    const key = alertKey(alert);
    flashRef.current.add(key);
    while (flashRef.current.size > MAX_FLASH_IDS) {
      const oldest = flashRef.current.values().next().value;
      if (oldest === undefined) break;
      flashRef.current.delete(oldest);
    }

    setAlertStats(countAlerts(next));
    if (pausedRef.current) {
      // Paused: count it, don't insert it. The card appears on resume.
      setPendingAlerts((n) => n + 1);
    } else {
      setAlerts(next);
    }
  }, []);

  const handleMessage = useCallback(
    (ev: MessageEvent) => {
      const msg = parseStreamMsg(ev.data);
      if (msg === null) return;
      setLastEventMs(Date.now());
      switch (msg.type) {
        case 'hello':
          setServerTimeMs(typeof msg.server_time_ms === 'number' ? msg.server_time_ms : null);
          setConfig(msg.config ?? null);
          break;
        case 'status':
          setStatus(msg);
          break;
        case 'flow':
          setFlow(msg);
          break;
        case 'screen':
          setScreen(msg);
          break;
        case 'survey':
          setSurvey(msg);
          break;
        case 'alert':
          ingestAlert(msg);
          break;
        case 'heat':
          // Routed to the heat store, never into React state.
          heatRef.current?.(msg);
          break;
        case 'ticker':
          // The primary live price path; straight into the chart's series.
          tickerRef.current?.(msg);
          break;
        default:
          // Unknown discriminators are ignored so a newer backend cannot crash us.
          break;
      }
    },
    [ingestAlert],
  );

  useEffect(() => {
    let closed = false;
    let source: EventSource | null = null;

    // Seed the slow-cadence panels (screen: 60s, survey: 5min) so a freshly
    // opened page is not blank while it waits for the next push. Failures are
    // expected when the backend is down and are swallowed on purpose.
    void (async () => {
      const [seededScreen, seededSurvey] = await Promise.all([
        getJson<ScreenMsg>('/api/screen'),
        getJson<SurveyMsg>('/api/survey'),
      ]);
      if (closed) return;
      if (seededScreen !== null && Array.isArray(seededScreen.rows)) {
        setScreen((prev) => prev ?? seededScreen);
      }
      if (seededSurvey !== null && (Array.isArray(seededSurvey.setups) || seededSurvey.budget != null)) {
        setSurvey((prev) => prev ?? seededSurvey);
      }
    })();

    if (typeof EventSource === 'undefined') {
      setConnection('offline');
      return () => {
        closed = true;
      };
    }

    try {
      source = new EventSource('/api/events');
    } catch {
      source = null;
      setConnection('offline');
    }

    if (source !== null) {
      const es = source;
      es.onopen = () => setConnection('live');
      es.onerror = () => setConnection('offline');
      es.onmessage = handleMessage;
      for (const type of STREAM_EVENT_TYPES) {
        es.addEventListener(type, handleMessage as EventListener);
      }
    }

    return () => {
      closed = true;
      if (source !== null) {
        const es = source;
        es.onopen = null;
        es.onerror = null;
        es.onmessage = null;
        for (const type of STREAM_EVENT_TYPES) {
          es.removeEventListener(type, handleMessage as EventListener);
        }
        es.close();
      }
    };
  }, [handleMessage]);

  const setPaused = useCallback((next: boolean) => {
    pausedRef.current = next;
    setPausedState(next);
    if (!next) {
      // Resume: flush everything buffered while paused and drop the badge.
      setAlerts(bufferRef.current);
      setPendingAlerts(0);
    }
  }, []);

  const refreshScreen = useCallback(() => {
    void send('/api/screen/refresh', 'POST');
  }, []);

  const refreshSurvey = useCallback(() => {
    void send('/api/survey/refresh', 'POST');
  }, []);

  const clearAlerts = useCallback(() => {
    void (async () => {
      const ok = await send('/api/alerts', 'DELETE');
      if (!ok) return;
      bufferRef.current = [];
      flashRef.current.clear();
      setAlerts([]);
      setPendingAlerts(0);
      setAlertStats(EMPTY_ALERT_STATS);
    })();
  }, []);

  return {
    connection,
    serverTimeMs,
    lastEventMs,
    config,
    status,
    flow,
    screen,
    survey,
    alerts,
    alertStats,
    flashIds: flashRef.current,
    paused,
    pendingAlerts,
    setPaused,
    refreshScreen,
    refreshSurvey,
    clearAlerts,
  };
}
