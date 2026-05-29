"use client";

import { useEffect, useState } from "react";
import { ensureAuthHeader } from "@/lib/auth";

const PROTECTED_PREFIXES = ["/generated-images/", "/api/images/", "/generated-audio/", "/generated-files/"];

export function isProtectedPath(url: string): boolean {
  return getProtectedMediaUrl(url) !== null;
}

function buildFullUrl(path: string): string {
  const apiBaseUrl =
    process.env.NEXT_PUBLIC_API_URL ||
    (process.env.NODE_ENV === "development" ? "http://localhost:8000" : "");
  return `${apiBaseUrl}${path}`;
}

export function getProtectedMediaUrl(url: string): string | null {
  if (PROTECTED_PREFIXES.some((prefix) => url.startsWith(prefix))) {
    return buildFullUrl(url);
  }

  const apiBaseUrl = buildFullUrl("");
  if (!apiBaseUrl) return null;

  try {
    const parsedUrl = new URL(url);
    const parsedApiBase = new URL(apiBaseUrl);
    if (parsedUrl.origin !== parsedApiBase.origin) return null;
    return PROTECTED_PREFIXES.some((prefix) => parsedUrl.pathname.startsWith(prefix)) ? url : null;
  } catch {
    return null;
  }
}

interface UseAuthenticatedImageUrlResult {
  displayUrl: string | null;
  loading: boolean;
  error: boolean;
}

export function useAuthenticatedImageUrl(rawUrl: string | null | undefined): UseAuthenticatedImageUrlResult {
  const [displayUrl, setDisplayUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!rawUrl) {
      setDisplayUrl(null);
      setLoading(false);
      setError(false);
      return;
    }

    const protectedUrl = getProtectedMediaUrl(rawUrl);
    const fullUrl = protectedUrl ?? rawUrl;

    if (!protectedUrl) {
      setDisplayUrl(fullUrl);
      setLoading(false);
      setError(false);
      return;
    }

    let revoked = false;
    let controller: AbortController | null = null;
    setLoading(true);
    setError(false);
    setDisplayUrl(null);

    async function loadBlob() {
      controller = new AbortController();
      try {
        const authHeader = await ensureAuthHeader();
        const headers: HeadersInit = {};
        if (authHeader) headers["Authorization"] = authHeader;

        const res = await fetch(fullUrl, { headers, signal: controller.signal });
        if (!res.ok) throw new Error(`fetch ${res.status}`);
        const blob = await res.blob();
        if (revoked) return;
        const objectUrl = URL.createObjectURL(blob);
        setDisplayUrl(objectUrl);
        setLoading(false);
      } catch {
        if (!revoked) {
          setError(true);
          setLoading(false);
        }
      }
    }

    loadBlob();

    return () => {
      revoked = true;
      controller?.abort();
      setDisplayUrl((current) => {
        if (current) URL.revokeObjectURL(current);
        return null;
      });
    };
  }, [rawUrl]);

  return { displayUrl, loading, error };
}
