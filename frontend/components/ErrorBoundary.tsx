'use client';

import { Component, ErrorInfo, ReactNode } from 'react';

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
  showDetails: boolean;
}

export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
      showDetails: false,
    };
  }

  static getDerivedStateFromError(_error: Error): Partial<ErrorBoundaryState> {
    return { hasError: true };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    console.error('ErrorBoundary caught an error:', error, errorInfo);
    this.setState({
      error,
      errorInfo,
    });
  }

  private handleReload = (): void => {
    window.location.reload();
  };

  private toggleDetails = (): void => {
    this.setState((prevState) => ({ showDetails: !prevState.showDetails }));
  };

  render(): ReactNode {
    if (this.state.hasError) {
      // Default fallback UI
      return (
        <div className="min-h-screen flex items-center justify-center p-4">
          <div className="max-w-md w-full bg-[var(--color-bg-secondary)] rounded-lg shadow-lg p-6 text-center">
            <div className="mb-4 flex justify-center">
              <div className="w-16 h-16 rounded-full bg-[var(--color-status-error-bg)] flex items-center justify-center">
                <svg fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
                  />
                </svg>
              </div>
            </div>

            <h2 className="text-xl font-semibold text-[--daemon-text-primary] mb-2">
              Something went wrong
            </h2>

            <p className="text-[--daemon-text-secondary] mb-4">
              The chat view encountered an error. Reloading should restore
              functionality.
            </p>

            {this.state.error && (
              <div className="mb-4 p-3 bg-[var(--color-bg-tertiary)] rounded text-left">
                <p className="font-mono text-sm text-[--daemon-text-primary] break-words">
                  {this.state.error.message}
                </p>
              </div>
            )}

            <button
              onClick={this.toggleDetails}
              className="text-sm text-[--daemon-text-secondary] hover:text-[--daemon-text-primary] mb-4 underline"
            >
              {this.state.showDetails ? 'Hide details' : 'Show details'}
            </button>

            {this.state.showDetails &&
              this.state.errorInfo &&
              this.state.error?.stack && (
                <div className="mb-4 p-3 bg-[var(--color-bg-tertiary)] rounded text-left overflow-auto max-h-48">
                  <p className="font-mono text-xs text-[var(--color-text-secondary)] whitespace-pre-wrap">
                    {this.state.error.stack}
                    {'\n\n'}
                    {this.state.errorInfo.componentStack}
                  </p>
                </div>
              )}

            <button
              onClick={this.handleReload}
              className="px-6 py-2 bg-[--daemon-accent] hover:bg-[--daemon-accent-hover] text-white font-medium rounded transition-colors"
            >
              Reload Page
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
