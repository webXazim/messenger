import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = { children: ReactNode };
type State = { hasError: boolean };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("UI crash captured by ErrorBoundary", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="auth-page">
          <div className="auth-card">
            <h1>Something went wrong</h1>
            <p className="muted">Refresh the page. If the problem continues, reopen the app after signing in again.</p>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
