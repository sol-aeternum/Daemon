"use client";

import { Suspense } from "react";
import { FolderKanban, Plus } from "lucide-react";
import { SidebarShell } from "@/components/SidebarShell";
import { ConversationHistoryProvider } from "@/components/ConversationHistoryProvider";

function ProjectsView() {
  return (
    <SidebarShell
      section="projects"
      title="Projects"
      headerAction={
        <button
          disabled
          className="hidden md:inline-flex items-center gap-2 rounded-xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-4 py-2.5 text-sm font-medium text-[var(--color-text-muted)] cursor-not-allowed"
        >
          <Plus className="h-4 w-4" />
          New project
        </button>
      }
    >
      <div className="mx-auto w-full max-w-5xl px-4 py-6 md:px-6 md:py-8">
        <div className="rounded-2xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] p-8 md:p-10">
          <div className="inline-flex h-12 w-12 items-center justify-center rounded-xl bg-[var(--color-accent-subtle)] text-[var(--color-accent-primary)]">
            <FolderKanban className="h-6 w-6" />
          </div>
          <h2 className="mt-4 text-2xl font-semibold text-[var(--color-text-primary)]">Project workspace</h2>
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[var(--color-text-secondary)]">
            Projects are coming next. This page is now wired into the sidebar and ready for scoped project views,
            grouped conversations, and project-level memory.
          </p>
          <p className="mt-3 text-xs text-[var(--color-text-muted)]">
            Out of scope for this pass: project data model and backend APIs.
          </p>
        </div>
      </div>
    </SidebarShell>
  );
}

export default function ProjectsPage() {
  return (
    <Suspense fallback={<div className="flex h-screen items-center justify-center">Loading projects...</div>}>
      <ConversationHistoryProvider>
        <ProjectsView />
      </ConversationHistoryProvider>
    </Suspense>
  );
}
