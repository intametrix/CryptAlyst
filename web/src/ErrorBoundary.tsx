import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  /** What was being rendered, for the report and the on-screen message. */
  where: string;
  children: ReactNode;
}

interface State {
  error: Error | null;
  info: string;
}

/**
 * Catches a render error and shows it instead of blanking the page.
 *
 * A React render error unmounts the whole tree, which is why a single broken
 * field turns the dashboard into a white screen with no explanation. This
 * boundary keeps the rest of the app usable and reports the failure to the
 * server, so it can be read from the log rather than guessed at from a
 * screenshot.
 */
export class ErrorBoundary extends Component<Props, State> {
  override state: State = { error: null, info: '' };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    const stack = String(info.componentStack ?? '').slice(0, 4000);
    this.setState({ info: stack });
    try {
      void fetch('/api/client-error', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          where: this.props.where,
          message: error.message,
          stack: String(error.stack ?? '').slice(0, 4000),
          component_stack: stack,
          href: window.location.href,
        }),
      });
    } catch {
      /* reporting must never throw over the error being reported */
    }
  }

  override render(): ReactNode {
    const { error, info } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="err-boundary" role="alert">
        <h2>something broke in “{this.props.where}”</h2>
        <p className="err-msg">{error.message}</p>
        <p className="err-hint">
          the rest of the dashboard still works — this panel failed to render. The error was
          reported to the server log.
        </p>
        {info && <pre className="err-stack">{info}</pre>}
        <button type="button" className="btn" onClick={() => this.setState({ error: null, info: '' })}>
          try again
        </button>
      </div>
    );
  }
}
