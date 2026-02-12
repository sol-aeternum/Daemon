import { useState } from 'react';
import { AgentState } from '../hooks/useAgentStatus';
import { AgentStatusCard } from './AgentStatusCard';

interface AgentStatusListProps {
  agents: AgentState[];
}

export function AgentStatusList({ agents }: AgentStatusListProps) {
  const [dismissedIds, setDismissedIds] = useState<Set<string>>(new Set());

  const handleDismiss = (id: string) => {
    setDismissedIds(prev => {
      const next = new Set(prev);
      next.add(id);
      return next;
    });
  };

  const activeAgents = agents.filter(agent => !dismissedIds.has(agent.id));

  if (activeAgents.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col items-end max-h-[80vh] w-full max-w-xs pointer-events-none">
      <div className="pointer-events-auto overflow-y-auto w-full pr-2 space-y-4">
        {activeAgents.map(agent => (
          <AgentStatusCard 
            key={agent.id} 
            agent={agent} 
            onDismiss={handleDismiss} 
          />
        ))}
      </div>
      <div className="mt-2 bg-gray-800 text-white text-xs px-2 py-1 rounded-full opacity-75 pointer-events-auto">
        {activeAgents.length} active agent{activeAgents.length !== 1 ? 's' : ''}
      </div>
    </div>
  );
}
