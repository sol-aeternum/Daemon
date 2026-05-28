"use client";

import { useCallback, useRef } from "react";
import { useStudio } from "../StudioProvider";
import { getAuthHeader } from "@/lib/auth";

type VideoSourceMode = "text-to-video" | "image-to-video";
type VideoTier = "starter" | "pro" | "max" | "byok";
type VideoProvider = "xai" | "kling";
type KlingModel = "kling-v3-pro" | "kling-o3-pro";

interface GenerateVideoOptions {
  duration: number;
  sourceMode: VideoSourceMode;
  tier: VideoTier;
  userId: string;
  provider?: VideoProvider;
  estimatedCredits?: number;
  klingModel?: KlingModel;
  audioEnabled?: boolean;
}

type JsonObject = Record<string, unknown>;

interface ToolResultExtract {
  videoUrl?: string;
  durationSeconds?: number;
  cost?: number;
  refunded?: boolean;
  error?: string;
}

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

function toObject(value: unknown): JsonObject | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as JsonObject;
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim().length > 0 ? value : undefined;
}

function asNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function asBoolean(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function parseJsonIfString(value: unknown): unknown {
  if (typeof value !== "string") {
    return value;
  }
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return value;
  }
}

function extractToolResult(result: unknown): ToolResultExtract {
  const parsed = parseJsonIfString(result);
  const root = toObject(parsed);
  if (!root) {
    return {};
  }

  const dataObj = toObject(root.data) ?? root;
  const metadataObj = toObject(root.metadata);

  const videoObj = toObject(dataObj.video);
  const videoUrl =
    asString(dataObj.video_url)
    ?? asString(dataObj.url)
    ?? asString(dataObj.file_url)
    ?? asString(videoObj?.url);

  const durationSeconds =
    asNumber(dataObj.duration_seconds)
    ?? asNumber(dataObj.duration)
    ?? asNumber(metadataObj?.duration);

  const cost = asNumber(metadataObj?.cost) ?? asNumber(dataObj.cost);

  const refunded =
    asBoolean(dataObj.refunded)
    ?? asBoolean(metadataObj?.refunded)
    ?? (root.success === false ? true : undefined);

  const error =
    asString(dataObj.error)
    ?? asString(root.error)
    ?? (root.success === false ? "Video generation failed" : undefined);

  return {
    videoUrl,
    durationSeconds,
    cost,
    refunded,
    error,
  };
}

function parseSseFrame(frame: string): { event: string; dataText: string } {
  const lines = frame.split("\n");
  let event = "message";
  let dataText = "";

  for (const line of lines) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
      continue;
    }
    if (line.startsWith("data:")) {
      dataText += `${line.slice(5).trim()}\n`;
    }
  }

  return { event, dataText: dataText.trim() };
}

function buildVideoRequestMessage(options: {
  prompt: string;
  duration: number;
  sourceMode: VideoSourceMode;
}): string {
  const lines = [
    "Generate a video.",
    `Prompt: ${options.prompt}`,
    "Use the Studio video settings from request metadata.",
    `Duration seconds: ${options.duration}`,
    `Source mode: ${options.sourceMode}`,
  ];

  lines.push("Return completion or failure details.");
  return lines.join("\n");
}

export function useVideoGeneration() {
  const { prompt, referenceImage, setIsGenerating, upsertGeneration } = useStudio();
  const inFlightRef = useRef(false);

  const generateVideo = useCallback(
    async ({ duration, sourceMode, tier, userId, provider, estimatedCredits, klingModel, audioEnabled }: GenerateVideoOptions) => {
      if (inFlightRef.current) {
        return;
      }

      const trimmedPrompt = prompt.trim();
      if (!trimmedPrompt) {
        return;
      }

      const generationId = `video:${Date.now()}`;
      const createdAt = new Date().toISOString();
      const referenceImageUrl = referenceImage?.url;
      const referenceImageId = referenceImage?.id;

        const modelId = provider === "kling" ? klingModel ?? "kling-o3-pro" : "xai-grok-imagine-3-video";
        const modelName = provider === "kling" ? "Kling 3.0" : "xAI Grok Imagine 3";

        upsertGeneration({
          id: generationId,
          mediaType: "video",
          modelId,
          modelName,
        prompt: trimmedPrompt,
        aspectRatio: "16:9",
        resolution: "video",
        durationSeconds: duration,
        costEstimate: estimatedCredits,
        status: "queued",
        createdAt,
      });

      setIsGenerating(true);

      let didComplete = false;
      let latestError: string | undefined;
      inFlightRef.current = true;

      try {
        const message = buildVideoRequestMessage({
          prompt: trimmedPrompt,
          duration,
          sourceMode,
        });

        const apiBaseUrl = getApiBaseUrl();
        const candidates = apiBaseUrl
          ? [`${apiBaseUrl}/chat`, `${apiBaseUrl}/api/chat`, "/api/chat", "/chat"]
          : ["/api/chat", "/chat"];

        let response: Response | null = null;
        for (let index = 0; index < candidates.length; index += 1) {
          const candidate = candidates[index];
          const candidateResponse = await fetch(candidate, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              ...getAuthHeaders(),
            },
            body: JSON.stringify({
              message,
              model: "auto",
              messages: [{ role: "user", content: message }],
              metadata: {
                video_generation: {
                  duration,
                  source_mode: sourceMode,
                  tier,
                  user_id: userId,
                  provider,
                  reference_image_url: referenceImageUrl,
                  reference_image_id: referenceImageId,
                  kling_model: provider === "kling" ? klingModel : undefined,
                  audio_enabled: provider === "kling" ? audioEnabled : undefined,
                },
              },
            }),
          });

          if (candidateResponse.status === 404 && index < candidates.length - 1) {
            continue;
          }
          response = candidateResponse;
          break;
        }

        if (!response || !response.ok || !response.body) {
          throw new Error(`Video generation request failed (${response?.status ?? "unknown"})`);
        }

        upsertGeneration({ id: generationId, status: "generating" });

        const decoder = new TextDecoder();
        const reader = response.body.getReader();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) {
            break;
          }

          buffer += decoder.decode(value, { stream: true });
          const frames = buffer.split("\n\n");
          buffer = frames.pop() || "";

          for (const frame of frames) {
            const { event, dataText } = parseSseFrame(frame);
            if (!dataText) {
              continue;
            }

            let payload: unknown = null;
            try {
              payload = JSON.parse(dataText) as unknown;
            } catch {
              continue;
            }

            const payloadObj = toObject(payload);
            const dataObj = toObject(payloadObj?.data);

            if (event === "video_generating") {
              upsertGeneration({ id: generationId, status: "generating" });
              continue;
            }

            if (event === "video_complete") {
              const videoUrl = asString(dataObj?.url) ?? asString(payloadObj?.url);
              const completionDuration = asNumber(dataObj?.duration) ?? asNumber(payloadObj?.duration) ?? duration;
              if (videoUrl) {
                upsertGeneration({
                  id: generationId,
                  status: "complete",
                  mediaType: "video",
                  videoUrl,
                  durationSeconds: completionDuration,
                  refunded: false,
                  error: undefined,
                });
                didComplete = true;
              }
              continue;
            }

            if (event === "video_failed") {
              const failedMessage = asString(dataObj?.error) ?? asString(payloadObj?.error) ?? "Video generation failed";
              const refunded = asBoolean(dataObj?.refunded) ?? asBoolean(payloadObj?.refunded);
              upsertGeneration({
                id: generationId,
                status: "error",
                error: failedMessage,
                refunded: refunded ?? false,
              });
              latestError = failedMessage;
              continue;
            }

            if (event === "tool_result") {
              const extracted = extractToolResult(dataObj?.result ?? payloadObj?.result);
              if (extracted.videoUrl) {
                upsertGeneration({
                  id: generationId,
                  status: "complete",
                  mediaType: "video",
                  videoUrl: extracted.videoUrl,
                  durationSeconds: extracted.durationSeconds ?? duration,
                  costEstimate: extracted.cost,
                  refunded: extracted.refunded ?? false,
                  error: undefined,
                });
                didComplete = true;
              } else if (extracted.error) {
                upsertGeneration({
                  id: generationId,
                  status: "error",
                  error: extracted.error,
                  refunded: extracted.refunded ?? false,
                });
                latestError = extracted.error;
              }
              continue;
            }

            if (event === "error") {
              const streamError = asString(dataObj?.error) ?? asString(payloadObj?.error) ?? "Video generation failed";
              upsertGeneration({
                id: generationId,
                status: "error",
                error: streamError,
              });
              latestError = streamError;
            }
          }
        }

        if (!didComplete) {
          upsertGeneration({
            id: generationId,
            status: "error",
            error: latestError ?? "Video generation did not return a playable video",
          });
        }
      } catch (error) {
        upsertGeneration({
          id: generationId,
          status: "error",
          error: error instanceof Error ? error.message : "Video generation failed",
        });
      } finally {
        inFlightRef.current = false;
        setIsGenerating(false);
        if (typeof window !== "undefined") {
          window.dispatchEvent(new CustomEvent("video-credits:refresh"));
        }
      }
    },
    [prompt, referenceImage, setIsGenerating, upsertGeneration],
  );

  return { generateVideo };
}
