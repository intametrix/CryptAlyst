import { useState } from 'react';
import { useResource } from '../hooks/useResource';

interface Report {
  where: string;
  message: string;
  href: string;
  at_ms: number;
}

/**
 * A visible count of render failures.
 *
 * The ErrorBoundary already reports each one to the server; until now it went to
 * the log, which nobody reads while looking at a grey panel. This is the same
 * report, surfaced: a count in the header, and the details on click.
 */
export function ClientErrorBadge() {
  const [open, setOpen] = useState(false);
  const resource = useResource<{ errors?: Report[] }>('/api/client-errors', { pollMs: 10000 });
  const reports = resource.data?.errors ?? [];

  if (reports.length === 0) return null;

  return (
    <span className="ceb">
      <button
        type="button"
        className="ceb-btn"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        title={`${reports.length} render failure${reports.length === 1 ? '' : 's'} — click for detail`}
      >
        {reports.length} render {reports.length === 1 ? 'error' : 'errors'}
      </button>
      {open && (
        <span className="ceb-list" role="dialog" aria-label="render errors">
          {reports.slice(0, 8).map((e: Report) => (
            <span className="ceb-row" key={`${e.at_ms}-${e.where}`}>
              <span className="ceb-where">{e.where}</span>
              <span className="ceb-msg">{e.message}</span>
            </span>
          ))}
          <button
            type="button"
            className="ceb-clear"
            onClick={() => {
              void fetch('/api/client-errors/clear', { method: 'POST' }).then(() => {
                resource.reload();
                setOpen(false);
              });
            }}
          >
            dismiss
          </button>
        </span>
      )}
    </span>
  );
}
