/**
 * One place that talks to the API.
 *
 * Forty-one components called fetch directly, each deciding for itself what to do
 * with a non-200 response, a network failure, or a body that was not the shape it
 * expected. Some surfaced the error, some logged it, some left the previous value
 * on screen so a failed refresh looked exactly like a live one.
 */
export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/** Read one endpoint as JSON, with the failure modes normalised. */
export async function getJSON<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, { headers: { accept: 'application/json' }, signal });
  if (!res.ok) throw new ApiError(`HTTP ${res.status} for ${path}`, res.status);
  const body = (await res.json()) as T & { ok?: boolean; error?: string };
  if (body.ok === false) throw new ApiError(body.error ?? `request failed: ${path}`, res.status);
  return body as T;
}

/** POST with an optional body; returns the JSON reply. */
export async function postJSON<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, {
    method: 'POST',
    headers: body === undefined ? { accept: 'application/json' } : { accept: 'application/json', 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  if (!res.ok) throw new ApiError(`HTTP ${res.status} for ${path}`, res.status);
  return (await res.json()) as T;
}
