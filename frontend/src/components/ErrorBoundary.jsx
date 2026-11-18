import { Component } from 'react';

/** Top-level error boundary (F-m5): one render crash used to whitescreen
 * the whole app. Now the route tree shows a recoverable message instead. */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error) {
    // Deliberately console-only: there is no error-reporting endpoint.
    console.error('Uncaught render error:', error);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="min-h-screen flex items-center justify-center bg-slate-50 px-4">
          <div className="max-w-md w-full rounded-xl border border-slate-200 bg-white p-6 text-center">
            <h1 className="font-bold text-slate-900 text-lg mb-2">Something went wrong</h1>
            <p className="text-sm text-slate-500 mb-4">
              This page hit an unexpected error. Your data is safe — try reloading the page.
            </p>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="text-sm font-semibold bg-brand-600 hover:bg-brand-700 text-white px-4 py-2 rounded-lg transition-colors"
            >
              Reload page
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
