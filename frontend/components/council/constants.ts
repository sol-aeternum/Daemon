import { Brain, Target, Shield, Zap, ShieldAlert } from "lucide-react";

export interface RoleConfig {
  name: string;
  icon: React.ComponentType<React.SVGProps<SVGSVGElement>>;
  color: string;
  borderColor: string;
  bgColor: string;
  description: string;
}

export const ROSTER_CONFIG: Record<string, RoleConfig> = {
  analyst: {
    name: "Analyst",
    icon: Brain,
    color: "var(--color-accent-primary)",
    borderColor: "var(--color-accent-primary)",
    bgColor: "bg-[var(--color-accent-primary)]/20",
    description: "Provides balanced, data-driven analysis"
  },
  strategist: {
    name: "Strategist",
    icon: Target,
    color: "var(--color-status-info)",
    borderColor: "var(--color-status-info)",
    bgColor: "bg-[var(--color-status-info)]/20",
    description: "Focuses on long-term planning and strategy"
  },
  skeptic: {
    name: "Skeptic",
    icon: Shield,
    color: "var(--color-status-success)",
    borderColor: "var(--color-status-success)",
    bgColor: "bg-[var(--color-status-success)]/20",
    description: "Questions assumptions and identifies risks"
  },
  contrarian: {
    name: "Contrarian",
    icon: Zap,
    color: "var(--color-status-warning)",
    borderColor: "var(--color-status-warning)",
    bgColor: "bg-[var(--color-status-warning)]/20",
    description: "Challenges conventional thinking and explores alternatives"
  },
  auditor: {
    name: "Auditor",
    icon: ShieldAlert,
    color: "var(--color-status-error)",
    borderColor: "var(--color-status-error)",
    bgColor: "bg-[var(--color-status-error)]/20",
    description: "Reviews findings for accuracy and compliance"
  }
};