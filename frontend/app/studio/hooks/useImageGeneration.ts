"use client";

import { useCallback } from "react";
import { useStudio } from "../StudioProvider";
import { ensureAuthHeader } from "@/lib/auth";

interface GenerationPayload {
  model_id: string;
  status: "queued" | "generating" | "complete" | "error";
  error?: string;
  result?: {
    model_id: string;
    image_id: string;
    image_url: string;
    generation_time_ms?: number;
    cost_estimate?: number;
    width?: number | null;
    height?: number | null;
  };
}

function parseSseFrames(chunk: string): Array<{ event: string; data: string }> {
  return chunk
    .split("\n\n")
    .map((frame) => frame.trim())
    .filter(Boolean)
    .map((frame) => {
      const eventMatch = frame.match(/^event:\s*(.+)$/m);
      const dataMatch = frame.match(/^data:\s*(.+)$/m);
      return {
        event: eventMatch ? eventMatch[1].trim() : "message",
        data: dataMatch ? dataMatch[1].trim() : "{}",
      };
    });
}

async function urlToBase64(url: string): Promise<string> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to fetch reference URL: ${response.status}`);
  }
  const buffer = await response.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.byteLength; i += 1) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

export function useImageGeneration() {
  const {
    selectedModels,
    prompt,
    referenceImage,
    aspectRatio,
    resolution,
    availableModels,
    setIsGenerating,
    upsertGeneration,
  } = useStudio();

  const generate = useCallback(async () => {
    if (selectedModels.length === 0 || prompt.trim().length === 0) {
      return;
    }

    const batchTimestamp = new Date().toISOString();
    const batchPrefix = `${Date.now()}`;

    const modelById = new Map(availableModels.map((model) => [model.id, model]));
    for (const modelId of selectedModels) {
      upsertGeneration({
        id: `${batchPrefix}:${modelId}`,
        modelId,
        modelName: modelById.get(modelId)?.name || modelId,
        prompt,
        aspectRatio,
        resolution,
        status: "queued",
        createdAt: batchTimestamp,
      });
    }

    setIsGenerating(true);
    try {
      const requestPayload: {
        models: string[];
        prompt: string;
        aspect_ratio: string;
        resolution: string;
        reference_id?: string;
        reference_image_b64?: string;
      } = {
        models: selectedModels,
        prompt,
        aspect_ratio: aspectRatio,
        resolution,
      };

      if (referenceImage) {
        if (referenceImage.id.startsWith("url:")) {
          requestPayload.reference_image_b64 = await urlToBase64(referenceImage.url);
        } else {
          requestPayload.reference_id = referenceImage.id;
        }
      }

      const authHeader = await ensureAuthHeader();
      const hdrs = new Headers();
      hdrs.set("Content-Type", "application/json");
      if (authHeader) hdrs.set("Authorization", authHeader);
      const response = await fetch("/api/images/generate", {
        method: "POST",
        headers: hdrs,
        body: JSON.stringify(requestPayload),
      });

      if (!response.ok || !response.body) {
        throw new Error(`Generation request failed: ${response.status}`);
      }

      const decoder = new TextDecoder();
      const reader = response.body.getReader();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }

        buffer += decoder.decode(value, { stream: true });
        const frameChunks = buffer.split("\n\n");
        buffer = frameChunks.pop() || "";

        for (const chunk of frameChunks) {
          const frames = parseSseFrames(chunk);
          for (const frame of frames) {
            if (frame.event === "done") {
              continue;
            }
            if (frame.event === "error") {
              const fallbackMessage = (() => {
                try {
                  const parsed = JSON.parse(frame.data) as { error?: string };
                  return parsed.error || "Unknown generation error";
                } catch {
                  return "Unknown generation error";
                }
              })();
              for (const modelId of selectedModels) {
                upsertGeneration({
                  id: `${batchPrefix}:${modelId}`,
                  status: "error",
                  error: fallbackMessage,
                });
              }
              continue;
            }

            let payload: GenerationPayload;
            try {
              payload = JSON.parse(frame.data) as GenerationPayload;
            } catch {
              continue;
            }

            const itemId = `${batchPrefix}:${payload.model_id}`;
            if (payload.status === "complete" && payload.result) {
              upsertGeneration({
                id: itemId,
                status: "complete",
                imageId: payload.result.image_id,
                imageUrl: payload.result.image_url,
                generationTimeMs: payload.result.generation_time_ms,
                costEstimate: payload.result.cost_estimate,
              });
            } else if (payload.status === "error") {
              upsertGeneration({
                id: itemId,
                status: "error",
                error: payload.error || "Model generation failed",
              });
            } else {
              upsertGeneration({ id: itemId, status: payload.status });
            }
          }
        }
      }
    } finally {
      setIsGenerating(false);
    }
  }, [
    selectedModels,
    prompt,
    referenceImage,
    aspectRatio,
    resolution,
    availableModels,
    setIsGenerating,
    upsertGeneration,
  ]);

  return { generate };
}
