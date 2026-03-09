"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Download, ExternalLink, ImageIcon, Maximize2, Music2, RefreshCw, X } from "lucide-react";
import { SidebarShell } from "@/components/SidebarShell";
import {
  ConversationHistoryProvider,
  useConversationHistoryContext,
} from "@/components/ConversationHistoryProvider";

type ArtifactKind = "image" | "audio";

interface ArtifactItem {
  path: string;
  kind: ArtifactKind;
  conversationId: string;
  conversationTitle: string;
  updatedAt: string;
}

const IMAGE_PATTERN = /(?:https?:\/\/[^\s]+)?(\/generated-images\/[^\s`"')]+)/g;
const AUDIO_PATTERN = /(?:https?:\/\/[^\s]+)?(\/generated-audio\/[^\s`"')]+)/g;

const extractPaths = (content: string, pattern: RegExp): string[] => {
  const matches: string[] = [];
  for (const match of content.matchAll(pattern)) {
    if (match[1]) {
      matches.push(match[1]);
    }
  }
  return matches;
};

function ArtifactsView() {
  const router = useRouter();
  const { conversations, fetchConversationById } = useConversationHistoryContext();
  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [lightboxImage, setLightboxImage] = useState<{ url: string; title: string } | null>(null);

  const apiBaseUrl =
    process.env.NEXT_PUBLIC_API_URL ||
    (process.env.NODE_ENV === "development" ? "http://localhost:8000" : "");

  const collectArtifacts = async () => {
    setIsLoading(true);

    const latestConversations = [...conversations]
      .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())
      .slice(0, 20);

    const details = await Promise.all(
      latestConversations.map(async (conversation) => {
        const fullConversation = await fetchConversationById(conversation.id);
        return {
          conversation,
          fullConversation,
        };
      }),
    );

    const dedupedArtifacts = new Map<string, ArtifactItem>();

    for (const { conversation, fullConversation } of details) {
      if (!fullConversation) {
        continue;
      }

      for (const message of fullConversation.messages) {
        const content = typeof message.content === "string" ? message.content : "";
        const imagePaths = extractPaths(content, IMAGE_PATTERN);
        const audioPaths = extractPaths(content, AUDIO_PATTERN);

        for (const path of imagePaths) {
          dedupedArtifacts.set(path, {
            path,
            kind: "image",
            conversationId: conversation.id,
            conversationTitle: conversation.title,
            updatedAt: conversation.updatedAt,
          });
        }

        for (const path of audioPaths) {
          dedupedArtifacts.set(path, {
            path,
            kind: "audio",
            conversationId: conversation.id,
            conversationTitle: conversation.title,
            updatedAt: conversation.updatedAt,
          });
        }
      }
    }

    const artifactList = [...dedupedArtifacts.values()].sort(
      (a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
    );

    setArtifacts(artifactList);
    setIsLoading(false);
  };

  useEffect(() => {
    collectArtifacts();
  }, [conversations]);

  const summary = useMemo(() => {
    const images = artifacts.filter((item) => item.kind === "image").length;
    const audio = artifacts.filter((item) => item.kind === "audio").length;
    return { images, audio };
  }, [artifacts]);

  return (
    <SidebarShell
      section="artifacts"
      title="Artifacts"
      headerAction={
        <button
          onClick={collectArtifacts}
          className="hidden md:inline-flex items-center gap-2 rounded-xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-4 py-2.5 text-sm font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-bg-hover)] transition-colors"
        >
          <RefreshCw className={`h-4 w-4 ${isLoading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      }
    >
      <div className="mx-auto w-full max-w-6xl px-4 py-6 md:px-6 md:py-8 space-y-5">
        <div className="rounded-2xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] p-5">
          <h2 className="text-xl font-semibold text-[var(--color-text-primary)]">Generated content library</h2>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
            Review generated images and audio from your recent conversations.
          </p>
          <div className="mt-3 inline-flex items-center gap-4 text-sm text-[var(--color-text-secondary)]">
            <span>{summary.images} images</span>
            <span>{summary.audio} audio files</span>
          </div>
        </div>

        {isLoading && (
          <div className="rounded-xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] p-5 text-sm text-[var(--color-text-muted)]">
            Scanning recent conversations for artifacts...
          </div>
        )}

        {!isLoading && artifacts.length === 0 && (
          <div className="rounded-xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] p-5 text-sm text-[var(--color-text-muted)]">
            No generated artifacts found yet.
          </div>
        )}

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {artifacts.map((artifact) => {
            const mediaUrl = `${apiBaseUrl}${artifact.path}`;

            return (
              <article
                key={`${artifact.conversationId}:${artifact.path}`}
                className="overflow-hidden rounded-2xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)]"
              >
                <div className="aspect-[16/10] w-full bg-[var(--color-bg-tertiary)] flex items-center justify-center overflow-hidden">
                  {artifact.kind === "image" ? (
                    <div className="relative group h-full w-full">
                      <img
                        src={mediaUrl}
                        alt={artifact.conversationTitle}
                        className="h-full w-full object-cover cursor-pointer hover:opacity-95 transition-opacity"
                        onClick={() =>
                          setLightboxImage({
                            url: mediaUrl,
                            title: artifact.conversationTitle || "Generated image",
                          })
                        }
                      />

                      <div className="absolute top-2 right-2 flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                        <button
                          onClick={(event) => {
                            event.stopPropagation();
                            setLightboxImage({
                              url: mediaUrl,
                              title: artifact.conversationTitle || "Generated image",
                            });
                          }}
                          className="p-1.5 bg-black/50 hover:bg-black/70 text-white rounded-md backdrop-blur-sm transition-colors"
                          title="Expand"
                        >
                          <Maximize2 className="h-4 w-4" />
                        </button>
                        <a
                          href={mediaUrl}
                          download={`artifact-image-${Date.now()}.png`}
                          className="p-1.5 bg-black/50 hover:bg-black/70 text-white rounded-md backdrop-blur-sm transition-colors"
                          title="Download"
                          target="_blank"
                          rel="noopener noreferrer"
                          onClick={(event) => event.stopPropagation()}
                        >
                          <Download className="h-4 w-4" />
                        </a>
                      </div>
                    </div>
                  ) : (
                    <div className="w-full px-4">
                      <div className="mb-3 inline-flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
                        <Music2 className="h-4 w-4" />
                        Audio artifact
                      </div>
                      <audio controls className="w-full" src={mediaUrl} />
                    </div>
                  )}
                </div>

                <div className="space-y-2 p-4">
                  <div className="inline-flex items-center gap-2 rounded-md bg-[var(--color-bg-tertiary)] px-2 py-1 text-xs text-[var(--color-text-secondary)]">
                    {artifact.kind === "image" ? <ImageIcon className="h-3.5 w-3.5" /> : <Music2 className="h-3.5 w-3.5" />}
                    {artifact.kind === "image" ? "Image" : "Audio"}
                  </div>
                  <p className="truncate text-sm font-medium text-[var(--color-text-primary)]">
                    {artifact.conversationTitle || "Conversation"}
                  </p>
                  <button
                    onClick={() => router.push(`/?id=${artifact.conversationId}`)}
                    className="inline-flex items-center gap-1.5 text-sm text-[var(--color-accent-primary)] hover:underline"
                  >
                    Open conversation
                    <ExternalLink className="h-3.5 w-3.5" />
                  </button>
                </div>
              </article>
            );
          })}
        </div>

        {lightboxImage && (
          <div
            className="fixed inset-0 z-[60] flex items-center justify-center bg-black/95 backdrop-blur-sm p-4 animate-in fade-in duration-200"
            onClick={() => setLightboxImage(null)}
          >
            <button
              onClick={() => setLightboxImage(null)}
              className="absolute top-4 right-4 p-2 text-white/70 hover:text-white bg-white/10 hover:bg-white/20 rounded-full transition-colors"
              title="Close"
            >
              <X className="h-6 w-6" />
            </button>

            <img
              src={lightboxImage.url}
              alt={lightboxImage.title}
              className="max-w-full max-h-full object-contain rounded-lg shadow-2xl"
              onClick={(event) => event.stopPropagation()}
            />

            <div
              className="absolute bottom-6 left-1/2 -translate-x-1/2 flex gap-4"
              onClick={(event) => event.stopPropagation()}
            >
              <a
                href={lightboxImage.url}
                download={`artifact-image-${Date.now()}.png`}
                className="flex items-center gap-2 px-4 py-2 bg-[var(--color-bg-inverse)] text-[var(--color-text-inverse)] rounded-full font-medium hover:bg-[var(--color-bg-hover)] transition-colors shadow-lg"
                target="_blank"
                rel="noopener noreferrer"
              >
                <Download className="h-4 w-4" />
                Download
              </a>
            </div>
          </div>
        )}
      </div>
    </SidebarShell>
  );
}

export default function ArtifactsPage() {
  return (
    <Suspense fallback={<div className="flex h-screen items-center justify-center">Loading artifacts...</div>}>
      <ConversationHistoryProvider>
        <ArtifactsView />
      </ConversationHistoryProvider>
    </Suspense>
  );
}
