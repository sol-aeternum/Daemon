'use client';

import {
  Search,
  Image,
  Code,
  MessageSquare,
  Sparkles,
  Users,
} from 'lucide-react';
import { useClientMounted } from '../hooks/useClientMounted';

interface WelcomeScreenProps {
  setInput: (input: string) => void;
  onDeliberate: () => void;
}

const getTimeGreeting = () => {
  const hour = new Date().getHours();
  if (hour >= 5 && hour < 12) {
    return 'Good morning';
  }
  if (hour >= 12 && hour < 17) {
    return 'Good afternoon';
  }
  return 'Good evening';
};

export function WelcomeScreen({ setInput, onDeliberate }: WelcomeScreenProps) {
  const isClientMounted = useClientMounted();
  const greeting = isClientMounted ? getTimeGreeting() : 'Good evening';

  const quickActions = [
    {
      icon: Search,
      label: 'Research',
      starter: 'I need to research...',
      gradient:
        'from-[var(--color-accent-primary)]/20 to-[var(--color-accent-hover)]/10',
      iconColor: 'text-[var(--color-accent-primary)]',
    },
    {
      icon: Image,
      label: 'Create Image',
      starter: 'Create an image of...',
      gradient:
        'from-[var(--color-accent-primary)]/20 to-[var(--color-accent-hover)]/10',
      iconColor: 'text-[var(--color-accent-primary)]',
    },
    {
      icon: Code,
      label: 'Write Code',
      starter: 'Write a function that...',
      gradient:
        'from-[var(--color-accent-primary)]/20 to-[var(--color-accent-hover)]/10',
      iconColor: 'text-[var(--color-accent-primary)]',
    },
    {
      icon: MessageSquare,
      label: 'Just Chat',
      starter: '',
      gradient:
        'from-[var(--color-accent-primary)]/20 to-[var(--color-accent-hover)]/10',
      iconColor: 'text-[var(--color-accent-primary)]',
    },
    {
      icon: Users,
      label: 'Deliberate',
      starter: '/council',
      description: '5-model Council debate · ~$0.40 / motion (varies)',
      gradient:
        'from-[var(--color-accent-primary)]/20 to-[var(--color-accent-hover)]/10',
      iconColor: 'text-[var(--color-accent-primary)]',
    },
  ];

  const handleActionClick = (starter: string) => {
    if (starter === '/council') {
      onDeliberate();
      return;
    }
    setInput(starter);
    // Focus the input after a short delay to allow state update
    setTimeout(() => {
      const inputEl = document.querySelector(
        'input[type="text"], textarea',
      ) as HTMLElement;
      inputEl?.focus();
      // If "Just Chat" was clicked (empty starter), don't submit
      if (starter) {
        // Let user continue typing, don't auto-submit
      }
    }, 50);
  };

  return (
    <div className="flex flex-col items-center justify-center min-h-full px-4 animate-fade-in">
      <div className="flex flex-col items-center max-w-2xl w-full space-y-8">
        {/* Logo / Wordmark */}
        <div className="flex items-center gap-3 mb-2">
          <div className="relative">
            <div className="absolute inset-0 bg-[var(--color-accent-primary)] blur-xl opacity-30 rounded-full" />
            <div className="relative w-12 h-12 rounded-xl bg-gradient-to-br from-[var(--color-accent-primary)] to-[var(--color-accent-hover)] flex items-center justify-center shadow-lg">
              <Sparkles className="w-6 h-6 text-[var(--color-text-on-accent)]" />
            </div>
          </div>
          <span className="text-3xl font-bold tracking-tight text-[var(--color-text-primary)]">
            Daemon
          </span>
        </div>

        {/* Greeting */}
        <div className="text-center space-y-2">
          <h1 className="text-4xl md:text-5xl font-bold text-[var(--color-text-primary)] tracking-tight">
            {greeting}
          </h1>
          <p className="text-lg text-[var(--color-text-muted)]">
            What would you like to do today?
          </p>
        </div>

        {/* Quick Action Chips */}
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-3 w-full mt-8">
          {quickActions.map((action, index) => {
            const Icon = action.icon;
            return (
              <button
                type="button"
                key={action.label}
                onClick={() => handleActionClick(action.starter)}
                className="group relative flex flex-col items-center gap-3 p-4 rounded-2xl bg-[var(--color-bg-secondary)] border border-[var(--color-border-primary)] hover:border-[var(--color-accent-primary)] transition-all duration-300 hover:shadow-lg hover:-translate-y-1 overflow-hidden"
                style={{
                  animationDelay: `${index * 100}ms`,
                }}
              >
                {/* Gradient background on hover */}
                <div
                  className={`absolute inset-0 bg-gradient-to-br ${action.gradient} opacity-0 group-hover:opacity-100 transition-opacity duration-300`}
                />

                {/* Icon */}
                <div className="relative z-10 w-10 h-10 rounded-xl bg-[var(--color-bg-tertiary)] group-hover:bg-[var(--color-bg-primary)] flex items-center justify-center transition-colors duration-300">
                  <Icon className={`w-5 h-5 ${action.iconColor}`} />
                </div>

                {/* Label */}
                <span className="relative z-10 text-sm font-medium text-[var(--color-text-secondary)] group-hover:text-[var(--color-text-primary)] transition-colors duration-300">
                  {action.label}
                </span>
                {action.description && (
                  <span className="relative z-10 text-xs text-[var(--color-text-muted)]">
                    {action.description}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* Hint text */}
        <p className="text-sm text-[var(--color-text-muted)] mt-8 text-center">
          Or type your message below to get started
        </p>
      </div>
    </div>
  );
}
