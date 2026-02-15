import Link from "next/link";
import { Bot, Zap, Shield, Code2, ArrowRight } from "lucide-react";

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-gpt-main text-gpt-text-primary flex flex-col">
      <nav className="w-full border-b border-white/10 bg-gpt-main/80 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 bg-gpt-accent rounded-lg flex items-center justify-center">
                <Bot className="w-5 h-5 text-white" />
              </div>
              <span className="font-bold text-xl tracking-tight">Daemon</span>
            </div>
            <div className="flex items-center gap-4">
              <Link 
                href="/login" 
                className="text-sm font-medium text-gpt-text-secondary hover:text-white transition-colors"
              >
                Log in
              </Link>
              <Link 
                href="/signup" 
                className="text-sm font-medium bg-gpt-accent hover:bg-opacity-90 text-white px-4 py-2 rounded-md transition-all"
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
              <h1 className="text-5xl md:text-7xl font-bold tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-white via-gpt-text-primary to-gpt-text-secondary pb-2">
                Your AI, Your Rules.
              </h1>
              <p className="text-xl md:text-2xl text-gpt-text-secondary max-w-2xl mx-auto leading-relaxed">
                Daemon is the open-source AI assistant that puts you in control. 
                Chat with any model, anywhere, anytime.
              </p>
              
              <div className="flex flex-col sm:flex-row items-center justify-center gap-4 pt-8 animate-slide-up" style={{ animationDelay: "100ms" }}>
                <Link 
                  href="/signup" 
                  className="w-full sm:w-auto px-8 py-4 bg-gpt-accent hover:bg-opacity-90 text-white rounded-lg font-semibold text-lg transition-all transform hover:scale-105 flex items-center justify-center gap-2 shadow-lg shadow-gpt-accent/20"
                >
                  Sign Up <ArrowRight className="w-5 h-5" />
                </Link>
                <Link 
                  href="/login" 
                  className="w-full sm:w-auto px-8 py-4 bg-gpt-input hover:bg-gpt-sidebar text-white rounded-lg font-semibold text-lg transition-all border border-white/10 hover:border-white/20 flex items-center justify-center"
                >
                  Login
                </Link>
              </div>
            </div>
          </div>
          
          <div className="absolute top-0 left-1/2 -translate-x-1/2 w-full h-full max-w-7xl pointer-events-none opacity-20">
            <div className="absolute top-20 left-20 w-72 h-72 bg-gpt-accent rounded-full blur-[128px]" />
            <div className="absolute bottom-20 right-20 w-96 h-96 bg-purple-500 rounded-full blur-[128px]" />
          </div>
        </section>

        <section className="py-24 bg-gpt-sidebar/50 border-t border-white/5">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
              <div className="p-8 rounded-2xl bg-gpt-main border border-white/5 hover:border-gpt-accent/50 transition-colors group animate-slide-up" style={{ animationDelay: "200ms" }}>
                <div className="w-12 h-12 bg-gpt-input rounded-xl flex items-center justify-center mb-6 group-hover:scale-110 transition-transform duration-300">
                  <Zap className="w-6 h-6 text-gpt-accent" />
                </div>
                <h3 className="text-xl font-bold mb-3 text-white">Model Agnostic</h3>
                <p className="text-gpt-text-secondary leading-relaxed">
                  Switch between OpenAI, Anthropic, and local models instantly. 
                  Use the best tool for the job without vendor lock-in.
                </p>
              </div>

              <div className="p-8 rounded-2xl bg-gpt-main border border-white/5 hover:border-gpt-accent/50 transition-colors group animate-slide-up" style={{ animationDelay: "300ms" }}>
                <div className="w-12 h-12 bg-gpt-input rounded-xl flex items-center justify-center mb-6 group-hover:scale-110 transition-transform duration-300">
                  <Shield className="w-6 h-6 text-gpt-accent" />
                </div>
                <h3 className="text-xl font-bold mb-3 text-white">Privacy First</h3>
                <p className="text-gpt-text-secondary leading-relaxed">
                  Your data stays yours. No hidden training, no tracking. 
                  Deploy locally or in your private cloud for complete control.
                </p>
              </div>

              <div className="p-8 rounded-2xl bg-gpt-main border border-white/5 hover:border-gpt-accent/50 transition-colors group animate-slide-up" style={{ animationDelay: "400ms" }}>
                <div className="w-12 h-12 bg-gpt-input rounded-xl flex items-center justify-center mb-6 group-hover:scale-110 transition-transform duration-300">
                  <Code2 className="w-6 h-6 text-gpt-accent" />
                </div>
                <h3 className="text-xl font-bold mb-3 text-white">Developer Friendly</h3>
                <p className="text-gpt-text-secondary leading-relaxed">
                  Built with modern tech stack (Next.js, FastAPI, Python). 
                  Easy to extend, customize, and integrate into your workflow.
                </p>
              </div>
            </div>
          </div>
        </section>
      </main>

      <footer className="bg-gpt-main border-t border-white/5 py-12">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex flex-col md:flex-row justify-between items-center gap-6">
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 bg-gpt-text-muted rounded-md flex items-center justify-center">
              <Bot className="w-4 h-4 text-gpt-main" />
            </div>
            <span className="font-semibold text-gpt-text-muted">Daemon</span>
          </div>
          <p className="text-sm text-gpt-text-muted">
            © {new Date().getFullYear()} Daemon. Open Source AI Assistant.
          </p>
        </div>
      </footer>
    </div>
  );
}
