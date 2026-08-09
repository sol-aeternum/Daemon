import Link from 'next/link';
import { Bot, Zap, Shield, Code2, ArrowRight } from 'lucide-react';

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-[var(--color-bg-tertiary)] text-[var(--color-text-primary)] flex flex-col">
      <nav className="w-full border-b border-[var(--color-border-primary)] bg-[var(--color-bg-tertiary)]/80 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 bg-daemon-accent rounded-lg flex items-center justify-center">
                <Bot className="w-5 h-5 text-white" />
              </div>
              <span className="font-bold text-xl tracking-tight">Daemon</span>
            </div>
            <div className="flex items-center gap-4">
              <Link
                href="/login"
                className="text-sm font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] transition-colors"
              >
                Log in
              </Link>
              <Link
                href="/signup"
                className="text-sm font-medium bg-daemon-accent hover:bg-opacity-90 text-white px-4 py-2 rounded-md transition-all"
              >
                Sign up
              </Link>
            </div>
          </div>
        </div>
      </nav>

      <main className="flex-grow">
        <section className="relative pt-20 pb-32 overflow-hidden">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 relative z-10 text-center">
            <div className="animate-fade-in space-y-8 max-w-4xl mx-auto">
              <h1 className="text-5xl md:text-7xl font-bold tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-[var(--color-text-primary)] via-[var(--color-text-secondary)] to-[var(--color-text-muted)] pb-2">
                Your AI, Your Rules.
              </h1>
              <p className="text-xl md:text-2xl text-daemon-text-secondary max-w-2xl mx-auto leading-relaxed">
                Daemon is the open-source AI assistant that puts you in control.
                Chat with any model, anywhere, anytime.
              </p>

              <div
                className="flex flex-col sm:flex-row items-center justify-center gap-4 pt-8 animate-slide-up"
                style={{ animationDelay: '100ms' }}
              >
                <Link
                  href="/signup"
                  className="w-full sm:w-auto px-8 py-4 bg-daemon-accent hover:bg-opacity-90 text-white rounded-lg font-semibold text-lg transition-all transform hover:scale-105 flex items-center justify-center gap-2 shadow-lg shadow-daemon-accent/20"
                >
                  Sign Up <ArrowRight className="w-5 h-5" />
                </Link>
                <Link
                  href="/login"
                  className="w-full sm:w-auto px-8 py-4 bg-[var(--color-bg-hover)] text-[var(--color-text-primary)] rounded-lg font-semibold text-lg transition-all border border-[var(--color-border-primary)] hover:border-[var(--color-border-secondary)] flex items-center justify-center"
                >
                  Login
                </Link>
              </div>
            </div>
          </div>

          <div className="absolute top-0 left-1/2 -translate-x-1/2 w-full h-full max-w-7xl pointer-events-none opacity-20">
            <div className="absolute top-20 left-20 w-72 h-72 bg-daemon-accent rounded-full blur-[128px]" />
            <div className="absolute bottom-20 right-20 w-96 h-96 bg-[var(--color-accent-primary)] rounded-full blur-[128px]" />
          </div>
        </section>

        <section className="py-24 bg-[var(--color-bg-secondary)]/50 border-t border-[var(--color-border-muted)]">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
              <div
                className="p-8 rounded-2xl bg-[var(--color-bg-tertiary)] border border-[var(--color-border-muted)] hover:border-[var(--color-accent-primary)]/50 transition-colors group animate-slide-up"
                style={{ animationDelay: '200ms' }}
              >
                <div className="w-12 h-12 bg-[var(--color-bg-hover)] rounded-xl flex items-center justify-center mb-6 group-hover:scale-110 transition-transform duration-300">
                  <Zap className="w-6 h-6 text-daemon-accent" />
                </div>
                <h3 className="text-xl font-bold mb-3 text-[var(--color-text-primary)]">
                  Model Agnostic
                </h3>
                <p className="text-daemon-text-secondary leading-relaxed">
                  Switch between OpenAI, Anthropic, and local models instantly.
                  Use the best tool for the job without vendor lock-in.
                </p>
              </div>

              <div
                className="p-8 rounded-2xl bg-[var(--color-bg-tertiary)] border border-[var(--color-border-muted)] hover:border-[var(--color-accent-primary)]/50 transition-colors group animate-slide-up"
                style={{ animationDelay: '300ms' }}
              >
                <div className="w-12 h-12 bg-[var(--color-bg-hover)] rounded-xl flex items-center justify-center mb-6 group-hover:scale-110 transition-transform duration-300">
                  <Shield className="w-6 h-6 text-daemon-accent" />
                </div>
                <h3 className="text-xl font-bold mb-3 text-[var(--color-text-primary)]">
                  Privacy First
                </h3>
                <p className="text-daemon-text-secondary leading-relaxed">
                  Your data stays yours. No hidden training, no tracking. Deploy
                  locally or in your private cloud for complete control.
                </p>
              </div>

              <div
                className="p-8 rounded-2xl bg-[var(--color-bg-tertiary)] border border-[var(--color-border-muted)] hover:border-[var(--color-accent-primary)]/50 transition-colors group animate-slide-up"
                style={{ animationDelay: '400ms' }}
              >
                <div className="w-12 h-12 bg-[var(--color-bg-hover)] rounded-xl flex items-center justify-center mb-6 group-hover:scale-110 transition-transform duration-300">
                  <Code2 className="w-6 h-6 text-daemon-accent" />
                </div>
                <h3 className="text-xl font-bold mb-3 text-[var(--color-text-primary)]">
                  Developer Friendly
                </h3>
                <p className="text-daemon-text-secondary leading-relaxed">
                  Built with modern tech stack (Next.js, FastAPI, Python). Easy
                  to extend, customize, and integrate into your workflow.
                </p>
              </div>
            </div>
          </div>
        </section>
      </main>

      <footer className="bg-[var(--color-bg-tertiary)] border-t border-[var(--color-border-muted)] py-12">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex flex-col md:flex-row justify-between items-center gap-6">
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 bg-[var(--color-text-muted)] rounded-md flex items-center justify-center">
              <Bot className="w-4 h-4 text-[var(--color-bg-tertiary)]" />
            </div>
            <span className="font-semibold text-[var(--color-text-muted)]">
              Daemon
            </span>
          </div>
          <p className="text-sm text-[var(--color-text-muted)]">
            © {new Date().getFullYear()} Daemon. Open Source AI Assistant.
          </p>
        </div>
      </footer>
    </div>
  );
}
