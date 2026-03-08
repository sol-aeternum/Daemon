import { useState, useEffect } from 'react';
import { AgentState } from '../hooks/useAgentStatus';

interface AgentStatusCardProps {
  agent: AgentState;
  onDismiss?: (id: string) => void;
  autoDismissDelay?: number; // ms
}

export function AgentStatusCard({ agent, onDismiss, autoDismissDelay = 5000 }: AgentStatusCardProps) {
  const [isVisible, setIsVisible] = useState(true);
  const [isExpanded, setIsExpanded] = useState(false);

  useEffect(() => {
    if (agent.status === 'completed' || agent.status === 'error') {
      const timer = setTimeout(() => {
        setIsVisible(false);
        onDismiss?.(agent.id);
      }, autoDismissDelay);
      return () => clearTimeout(timer);
    }
  }, [agent.status, agent.id, onDismiss, autoDismissDelay]);

  if (!isVisible) return null;

  const getIcon = (type: string) => {
    switch (type.toLowerCase()) {
      case 'research': return '🔍';
      case 'image': return '🖼️';
      case 'code': return '💻';
      case 'reader': return '📄';
      default: return '🤖';
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'running': return 'bg-[var(--color-accent-primary)]';
      case 'completed': return 'bg-[var(--color-status-success)]';
      case 'error': return 'bg-[var(--color-status-error)]';
      default: return 'bg-[var(--color-border-secondary)]';
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'running': return (
        <svg className="animate-spin h-4 w-4 text-[var(--color-accent-primary)]" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
      );
      case 'completed': return (
        <svg className="h-4 w-4 text-[var(--color-status-success)]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
        </svg>
      );
      case 'error': return (
        <svg className="h-4 w-4 text-[var(--color-status-error)]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      );
      default: return <span className="h-4 w-4 block rounded-full border-2 border-[var(--color-border-secondary)]"></span>;
    }
  };

  return (
    <div className="bg-[var(--color-bg-secondary)] rounded-lg shadow-lg border border-[var(--color-border-primary)] overflow-hidden w-full transition-all duration-300 ease-in-out transform hover:scale-105">
      <div 
        className="p-3 cursor-pointer hover:bg-[var(--color-bg-tertiary)] transition-colors"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <span className="text-xl">{getIcon(agent.type)}</span>
            <span className="font-medium text-sm text-[var(--color-text-secondary)] capitalize">{agent.type} Agent</span>
          </div>
          {getStatusIcon(agent.status)}
        </div>
        
        <div className="text-xs text-[var(--color-text-muted)] mb-2 truncate" title={agent.task}>
          {agent.task}
        </div>

        <div className="w-full bg-[var(--color-border-primary)] rounded-full h-1.5 mb-1">
          <div 
            className={`h-1.5 rounded-full transition-all duration-500 ${getStatusColor(agent.status)}`}
            style={{ width: `${agent.progress}%` }}
          ></div>
        </div>
      </div>

      {isExpanded && (
        <div className="px-3 pb-3 bg-[var(--color-bg-tertiary)] border-t border-[var(--color-border-muted)] text-xs text-[var(--color-text-secondary)]">
          <div className="mt-2">
            <span className="font-semibold">Status:</span> {agent.status}
          </div>
          {agent.message && (
            <div className="mt-1">
              <span className="font-semibold">Message:</span> {agent.message}
            </div>
          )}
          {agent.result && (
            <div className="mt-1">
              <span className="font-semibold">Result:</span> 
              <pre className="mt-1 p-2 bg-[var(--color-bg-tertiary)] rounded overflow-x-auto whitespace-pre-wrap max-h-32">
                {agent.result}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
