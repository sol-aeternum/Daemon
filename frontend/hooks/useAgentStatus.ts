import { useMemo } from 'react';
import { ChatEvent } from '../lib/events';

export type AgentStatus = "pending" | "running" | "completed" | "error";

export interface AgentState {
  id: string;
  type: string;
  task: string;
  status: AgentStatus;
  progress: number;
  message?: string;
  result?: string;
}

export function useAgentStatus(events: ChatEvent[]) {
  const agents = useMemo(() => {
    const agentMap: Record<string, AgentState> = {};

    events.forEach(event => {
      if (event.type === 'agent_spawn') {
        agentMap[event.agent] = {
          id: event.agent,
          type: event.agentType || 'unknown',
          task: event.task,
          status: 'pending',
          progress: 0,
        };
      } else if (event.type === 'agent_status') {
        if (agentMap[event.agent]) {
          agentMap[event.agent] = {
            ...agentMap[event.agent],
            status: event.status,
            progress: event.progress ?? agentMap[event.agent].progress,
            message: event.message ?? agentMap[event.agent].message,
          };
        }
      } else if (event.type === 'agent_complete') {
        if (agentMap[event.agent]) {
          agentMap[event.agent] = {
            ...agentMap[event.agent],
            status: 'completed',
            progress: 100,
            result: event.result,
          };
        }
      }
    });

    return Object.values(agentMap);
  }, [events]);

  return agents;
}
