"use client";

import { Suspense, useEffect } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ConversationHistoryProvider } from "@/components/ConversationHistoryProvider";
import { SidebarShell } from "@/components/SidebarShell";
import { StudioProvider, useStudio } from "./StudioProvider";
import { AspectRatioPicker } from "./components/AspectRatioPicker";
import { ImageGallery } from "./components/ImageGallery";
import { ModelSelector } from "./components/ModelSelector";
import { PromptInput } from "./components/PromptInput";
import { ReferenceUpload } from "./components/ReferenceUpload";
import { ResolutionPicker } from "./components/ResolutionPicker";

function StudioHydration() {
  const { setPrompt, setReferenceImage } = useStudio();
  const searchParams = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    const image = searchParams.get("image");
    const prompt = searchParams.get("prompt");
    if (!image && !prompt) {
      return;
    }

    if (prompt) {
      setPrompt(prompt);
    }
    if (image) {
      setReferenceImage({ id: `url:${image}`, url: image });
    }

    router.replace(pathname, { scroll: false });
  }, [pathname, router, searchParams, setPrompt, setReferenceImage]);

  return null;
}

function StudioControls() {
  return (
    <div className="space-y-4">
      <PromptInput />
      <ReferenceUpload />
      <ModelSelector />
      <AspectRatioPicker />
      <ResolutionPicker />
    </div>
  );
}

function StudioPageContent() {
  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-6 md:px-6 md:py-8">
      <StudioHydration />

      <details className="mb-4 rounded-xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] p-3 md:hidden">
        <summary className="cursor-pointer text-sm font-semibold text-[var(--color-text-primary)]">Studio Controls</summary>
        <div className="mt-3">
          <StudioControls />
        </div>
      </details>

      <div className="grid gap-4 md:grid-cols-[340px_1fr]">
        <aside className="hidden md:block">
          <StudioControls />
        </aside>
        <main>
          <ImageGallery />
        </main>
      </div>
    </div>
  );
}

function StudioView() {
  return (
    <SidebarShell section="studio" title="Studio">
      <StudioProvider>
        <StudioPageContent />
      </StudioProvider>
    </SidebarShell>
  );
}

export default function StudioPage() {
  return (
    <Suspense fallback={<div className="flex h-screen items-center justify-center">Loading studio...</div>}>
      <ConversationHistoryProvider>
        <StudioView />
      </ConversationHistoryProvider>
    </Suspense>
  );
}
