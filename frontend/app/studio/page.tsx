"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { Film, Image as ImageIcon, Loader2, RefreshCw } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ConversationHistoryProvider } from "@/components/ConversationHistoryProvider";
import { CreditBalance } from "@/components/CreditBalance";
import { SidebarShell } from "@/components/SidebarShell";
import { StudioProvider, useStudio } from "./StudioProvider";
import { AspectRatioPicker } from "./components/AspectRatioPicker";
import { ImageGallery } from "./components/ImageGallery";
import { ModelSelector } from "./components/ModelSelector";
import { PromptInput } from "./components/PromptInput";
import { ReferenceUpload } from "./components/ReferenceUpload";
import { ResolutionPicker } from "./components/ResolutionPicker";
import { useVideoGeneration } from "./hooks/useVideoGeneration";
import { getAuthHeader } from "@/lib/auth";

const DEFAULT_STUDIO_USER_ID = "00000000-0000-0000-0000-000000000001";
const VALID_VIDEO_TIERS = ["free", "starter", "pro", "max", "byok"] as const;
const VIDEO_ENABLED_TIERS = ["starter", "pro", "max", "byok"] as const;

type StudioMode = "image" | "video";
type VideoSourceMode = "text-to-video" | "image-to-video";
type VideoProvider = "xai" | "kling";
type KlingModel = "kling-v3-pro" | "kling-o3-pro";
type VideoTier = (typeof VALID_VIDEO_TIERS)[number];
type VideoEnabledTier = (typeof VIDEO_ENABLED_TIERS)[number];

type EstimateResponse = {
  credits_required?: number;
  current_balance?: number;
  sufficient?: boolean;
};

function getApiBaseUrl(): string {
  const fromEnv = process.env.NEXT_PUBLIC_API_URL || "";
  if (fromEnv.trim().length > 0) {
    return fromEnv.replace(/\/$/, "");
  }
  if (process.env.NODE_ENV === "development") {
    return "http://localhost:8000";
  }
  return "";
}

function getAuthHeaders(): HeadersInit {
  const header = getAuthHeader();
  if (!header) return {};
  return { Authorization: header };
}

function isVideoTier(value: string): value is VideoTier {
  return VALID_VIDEO_TIERS.includes(value as VideoTier);
}

function isVideoEnabledTier(value: VideoTier): value is VideoEnabledTier {
  return VIDEO_ENABLED_TIERS.includes(value as VideoEnabledTier);
}

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

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
      <PromptInput mode="image" />
      <ReferenceUpload />
      <ModelSelector />
      <AspectRatioPicker />
      <ResolutionPicker />
    </div>
  );
}

function StudioModeToggle({
  mode,
  setMode,
  videoEnabled,
}: {
  mode: StudioMode;
  setMode: (value: StudioMode) => void;
  videoEnabled: boolean;
}) {
  return (
    <section className="mb-4 rounded-xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] p-2">
      <div className="grid grid-cols-2 gap-2">
        <button
          type="button"
          onClick={() => setMode("image")}
          className={`inline-flex items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
            mode === "image"
              ? "bg-[var(--color-bg-hover)] text-[var(--color-text-primary)]"
              : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
          }`}
        >
          <ImageIcon className="h-4 w-4" />
          Image
        </button>
        <button
          type="button"
          disabled={!videoEnabled}
          onClick={() => setMode("video")}
          className={`inline-flex items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
            mode === "video" && videoEnabled
              ? "bg-[var(--color-bg-hover)] text-[var(--color-text-primary)]"
              : videoEnabled
                ? "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
                : "cursor-not-allowed text-[var(--color-text-muted)] opacity-60"
          }`}
          title={videoEnabled ? "" : "Video generation is available on Starter and above"}
        >
          <Film className="h-4 w-4" />
          Video
        </button>
      </div>
    </section>
  );
}

function RestrictedVideoModePanel({ tier }: { tier: VideoTier }) {
  return (
    <div className="space-y-4">
      <section className="rounded-2xl border border-[var(--color-status-warning)]/40 bg-[var(--color-status-warning-bg)]/30 p-4">
        <h2 className="text-sm font-semibold text-[var(--color-status-warning)]">Upgrade required for video mode</h2>
        <p className="mt-2 text-xs text-[var(--color-text-secondary)]">
          Video generation is disabled on the <strong className="font-semibold">{tier}</strong> tier.
          Upgrade to <strong className="font-semibold">Starter</strong> or higher to unlock video creation.
        </p>
      </section>

      <section className="rounded-2xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] p-4 opacity-60">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">Video options</h2>
        <p className="text-xs text-[var(--color-text-muted)]">Video controls are unavailable for this tier.</p>
      </section>
    </div>
  );
}

const DURATION_CHOICES = [5, 10, 15, 20, 30] as const;
const MAX_DURATION = 30;

function VideoModeControls({
  userId,
  tier,
}: {
  userId: string;
  tier: VideoEnabledTier;
}) {
  const { referenceImage } = useStudio();
  const { generateVideo } = useVideoGeneration();
  const [duration, setDuration] = useState<number>(5);
  const [sourceMode, setSourceMode] = useState<VideoSourceMode>("text-to-video");
  const [videoProvider, setVideoProvider] = useState<VideoProvider>("xai");
  const [klingModel, setKlingModel] = useState<KlingModel>("kling-o3-pro");
  const [audioEnabled, setAudioEnabled] = useState<boolean>(false);
  const [isEstimating, setIsEstimating] = useState(false);
  const [estimateError, setEstimateError] = useState<string | null>(null);
  const [estimate, setEstimate] = useState<{ required: number; balance: number; sufficient: boolean } | null>(null);
  const isKlingSelected = videoProvider === "kling";

  const apiBaseUrl = useMemo(() => getApiBaseUrl(), []);

  const loadEstimate = useCallback(async () => {
    if (!userId) {
      setEstimateError("Missing user ID");
      return;
    }

    setIsEstimating(true);
    setEstimateError(null);

    const query = new URLSearchParams({
      duration: String(duration),
      tier,
      user_id: userId,
      provider: videoProvider,
    });
    if (videoProvider === "kling") {
      query.set("kling_model", klingModel);
      query.set("audio_enabled", String(audioEnabled));
    }
    const proxyPath = `/api/video-credits/estimate?${query.toString()}`;
    const directPath = `/video-credits/estimate?${query.toString()}`;
    const candidates = apiBaseUrl
      ? [proxyPath, `${apiBaseUrl}${directPath}`, directPath]
      : [proxyPath, directPath];

    for (let index = 0; index < candidates.length; index += 1) {
      const candidate = candidates[index];
      try {
        const response = await fetch(candidate, {
          headers: {
            ...getAuthHeaders(),
          },
          cache: "no-store",
        });

        if (response.status === 404 && index < candidates.length - 1) {
          continue;
        }

        if (!response.ok) {
          throw new Error(`Estimate failed (${response.status})`);
        }

        const payload = (await response.json()) as EstimateResponse;
        setEstimate({
          required: typeof payload.credits_required === "number" ? payload.credits_required : 0,
          balance: typeof payload.current_balance === "number" ? payload.current_balance : 0,
          sufficient: Boolean(payload.sufficient),
        });
        setEstimateError(null);
        setIsEstimating(false);
        return;
      } catch (error) {
        if (index === candidates.length - 1) {
          setEstimateError(error instanceof Error ? error.message : "Failed to estimate video cost");
        }
      }
    }

    setIsEstimating(false);
  }, [apiBaseUrl, duration, tier, userId, videoProvider, klingModel, audioEnabled]);

  useEffect(() => {
    void loadEstimate();
  }, [loadEstimate]);

  const insufficientCredits = Boolean(estimate && !estimate.sufficient);
  const needsReferenceImage = sourceMode === "image-to-video" && !referenceImage;
  const videoGenerateDisabled = isEstimating || insufficientCredits || needsReferenceImage;
  const videoGenerateDisabledReason = needsReferenceImage
    ? "Add a reference image to use image-to-video mode."
    : insufficientCredits
      ? "Insufficient credits for the selected duration."
      : null;
  const videoButtonLabel = isEstimating
    ? "Estimating..."
    : estimate
      ? `Generate video (${estimate.required} credits)`
      : "Generate video";

  const handleGenerateVideo = useCallback(async () => {
    await generateVideo({
      duration,
      sourceMode,
      tier,
      userId,
      provider: videoProvider,
      estimatedCredits: estimate?.required,
      klingModel: videoProvider === "kling" ? klingModel : undefined,
      audioEnabled: videoProvider === "kling" ? audioEnabled : undefined,
    });
  }, [duration, estimate?.required, generateVideo, sourceMode, tier, userId, videoProvider, klingModel, audioEnabled]);

  return (
    <div className="space-y-4">
      <PromptInput
        mode="video"
        onGenerateVideo={handleGenerateVideo}
        videoGenerateDisabled={videoGenerateDisabled}
        videoGenerateDisabledReason={videoGenerateDisabledReason}
        videoButtonLabel={videoButtonLabel}
      />

      <section className="rounded-2xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] p-4">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">Video options</h2>

        <div className="space-y-3">
          <div>
            <p className="mb-2 text-xs font-medium text-[var(--color-text-secondary)]">Source</p>
            <div className="grid grid-cols-2 gap-2">
              {(["text-to-video", "image-to-video"] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  onClick={() => setSourceMode(option)}
                  className={`rounded-lg border px-3 py-2 text-xs font-medium transition-colors ${
                    sourceMode === option
                      ? "border-[var(--color-accent-primary)] bg-[var(--color-bg-hover)] text-[var(--color-text-primary)]"
                      : "border-[var(--color-border-primary)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
                  }`}
                >
                  {option === "text-to-video" ? "Text to Video" : "Image to Video"}
                </button>
              ))}
            </div>
          </div>

          {sourceMode === "image-to-video" && <ReferenceUpload />}

          <div>
            <p className="mb-2 text-xs font-medium text-[var(--color-text-secondary)]">Provider</p>
            <div className="grid grid-cols-2 gap-2">
              {([
                { id: "xai", label: "Grok Imagine" },
                { id: "kling", label: "Kling 3.0" },
              ] as const).map((option) => (
                <button
                  key={option.id}
                  type="button"
                  onClick={() => setVideoProvider(option.id)}
                  className={`rounded-lg border px-3 py-2 text-xs font-medium transition-colors ${
                    videoProvider === option.id
                      ? "border-[var(--color-accent-primary)] bg-[var(--color-bg-hover)] text-[var(--color-text-primary)]"
                      : "border-[var(--color-border-primary)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          {isKlingSelected && (
            <>
              <div>
                <p className="mb-2 text-xs font-medium text-[var(--color-text-secondary)]">Model</p>
                <div className="grid grid-cols-2 gap-2">
                  {[
                    { id: "kling-o3-pro", label: "O3 Pro" },
                    { id: "kling-v3-pro", label: "V3 Pro" },
                  ].map((option) => (
                    <button
                      key={option.id}
                      type="button"
                      onClick={() => setKlingModel(option.id as KlingModel)}
                      className={`rounded-lg border px-3 py-2 text-xs font-medium transition-colors ${
                        klingModel === option.id
                          ? "border-[var(--color-accent-primary)] bg-[var(--color-bg-hover)] text-[var(--color-text-primary)]"
                          : "border-[var(--color-border-primary)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
                      }`}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="flex items-center justify-between">
                <p className="text-xs font-medium text-[var(--color-text-secondary)]">Include audio</p>
                <button
                  type="button"
                  onClick={() => setAudioEnabled(!audioEnabled)}
                  className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                    audioEnabled
                      ? "bg-[var(--color-accent-primary)]"
                      : "bg-[var(--color-border-primary)]"
                  }`}
                >
                  <span
                    className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
                      audioEnabled ? "translate-x-5" : "translate-x-1"
                    }`}
                  />
                </button>
              </div>
            </>
          )}

          <div>
            <div className="mb-2 flex items-center justify-between">
              <p className="text-xs font-medium text-[var(--color-text-secondary)]">Duration</p>
            </div>

            <div className="mb-2 grid grid-cols-5 gap-2">
              {DURATION_CHOICES.map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setDuration(value)}
                  className={`rounded-lg border px-3 py-2 text-xs font-medium transition-colors ${
                    duration === value
                      ? "border-[var(--color-accent-primary)] bg-[var(--color-bg-hover)] text-[var(--color-text-primary)]"
                      : "border-[var(--color-border-primary)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
                  }`}
                >
                  {value}s
                </button>
              ))}
            </div>

            <input
              type="range"
              min={5}
              max={MAX_DURATION}
              step={5}
              value={duration}
              onChange={(event) => setDuration(Number(event.target.value))}
              className="w-full"
            />
          </div>

          <div className="rounded-lg border border-[var(--color-border-primary)] bg-[var(--color-bg-primary)] p-3">
            <div className="mb-2 flex items-center justify-between">
              <p className="text-xs font-medium text-[var(--color-text-secondary)]">Cost preview</p>
              <button
                type="button"
                onClick={() => {
                  void loadEstimate();
                }}
                className="inline-flex items-center gap-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)]"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${isEstimating ? "animate-spin" : ""}`} />
                Refresh
              </button>
            </div>

            {isEstimating && (
              <p className="inline-flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Fetching estimate...
              </p>
            )}

            {!isEstimating && estimate && (
              <div className="space-y-1 text-xs text-[var(--color-text-secondary)]">
                <p>Estimated cost: {estimate.required} credits</p>
                <p>Current balance: {estimate.balance} credits</p>
                <p className={estimate.sufficient ? "text-[var(--color-status-success)]" : "text-[var(--color-status-warning)]"}>
                  {estimate.sufficient ? "You have enough credits." : "Insufficient credits for this duration."}
                </p>
              </div>
            )}

            {estimateError && <p className="text-xs text-[var(--color-status-warning)]">{estimateError}</p>}
          </div>

          <div className="rounded-lg border border-[var(--color-border-primary)] bg-[var(--color-bg-primary)] px-3 py-2">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">Provider</p>
            <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
              {videoProvider === "kling" ? "Kling 3.0" : "xAI Grok Imagine 3"}
            </p>
          </div>
        </div>
      </section>

      <CreditBalance mode="expanded" userId={userId} refreshInterval={15_000} />
    </div>
  );
}

function StudioControlPanel({
  mode,
  userId,
  tier,
  videoEnabled,
}: {
  mode: StudioMode;
  userId: string;
  tier: VideoTier;
  videoEnabled: boolean;
}) {
  if (mode === "image") {
    return <StudioControls />;
  }
  if (!videoEnabled || !isVideoEnabledTier(tier)) {
    return <RestrictedVideoModePanel tier={tier} />;
  }
  return <VideoModeControls userId={userId} tier={tier} />;
}

function StudioPageContent({ userId, tier }: { userId: string; tier: VideoTier }) {
  const [mode, setMode] = useState<StudioMode>("image");
  const videoEnabled = isVideoEnabledTier(tier);

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-6 md:px-6 md:py-8">
      <StudioHydration />

      <StudioModeToggle mode={mode} setMode={setMode} videoEnabled={videoEnabled} />

      {!videoEnabled && (
        <section className="mb-4 rounded-xl border border-[var(--color-status-warning)]/40 bg-[var(--color-status-warning-bg)]/30 px-3 py-2">
          <p className="text-xs text-[var(--color-text-secondary)]">
            Video mode is locked on your current tier. Upgrade to <strong className="font-semibold">Starter</strong> or above to enable video generation.
          </p>
        </section>
      )}

      <details className="mb-4 rounded-xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] p-3 md:hidden">
        <summary className="cursor-pointer text-sm font-semibold text-[var(--color-text-primary)]">Studio Controls</summary>
        <div className="mt-3">
          <StudioControlPanel mode={mode} userId={userId} tier={tier} videoEnabled={videoEnabled} />
        </div>
      </details>

      <div className="grid gap-4 md:grid-cols-[340px_1fr]">
        <aside className="hidden md:block">
          <StudioControlPanel mode={mode} userId={userId} tier={tier} videoEnabled={videoEnabled} />
        </aside>
        <main>
          <ImageGallery />
        </main>
      </div>
    </div>
  );
}

function StudioView() {
  const [userId, setUserId] = useState(DEFAULT_STUDIO_USER_ID);
  const [tier, setTier] = useState<VideoTier>("starter");
  const videoEnabled = isVideoEnabledTier(tier);

  useEffect(() => {
    const storedUserId = localStorage.getItem("daemon_user_id") || "";
    if (isUuid(storedUserId)) {
      setUserId(storedUserId);
    }

    const storedTier = (localStorage.getItem("daemon_tier") || "").toLowerCase();
    if (isVideoTier(storedTier)) {
      setTier(storedTier);
    }
  }, []);

  return (
    <SidebarShell
      section="studio"
      title="Studio"
      headerAction={
        videoEnabled
          ? <CreditBalance mode="compact" userId={userId} refreshInterval={15_000} />
          : <span className="rounded-md border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-2 py-1 text-xs text-[var(--color-text-muted)]">Video locked</span>
      }
    >
      <StudioProvider>
        <StudioPageContent userId={userId} tier={tier} />
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
