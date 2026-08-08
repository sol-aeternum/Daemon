import { NextRequest, NextResponse } from 'next/server';

const STATIC_SECURITY_HEADERS: Record<string, string> = {
  'Strict-Transport-Security': 'max-age=63072000; includeSubDomains',
  'X-Frame-Options': 'DENY',
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'strict-origin-when-cross-origin',
  'Permissions-Policy': 'camera=(), microphone=(self), geolocation=()',
};

/**
 * Hostnames that are part of the daemon's runtime adjacency and must be
 * reachable from the browser. Each entry is justified by an actual call
 * site in the codebase (verified by tests in tests/test_security_headers_and_cors.py).
 */
const SCRIPT_SRC_EXTERNAL_HOSTS = [
  // Google Identity Services — loaded by frontend/components/AuthLanding.tsx
  // for hosted Google sign-in.
  'https://accounts.google.com',
];

const CONNECT_SRC_EXTERNAL_HOSTS = [
  // ElevenLabs streaming TTS and realtime STT — frontend/hooks/useStreamingTts.ts,
  // frontend/hooks/useStt.ts, frontend/components/TextToSpeechButton.tsx.
  'wss://api.elevenlabs.io',
];

// `media-src` is required so provider-hosted video/audio (e.g. fal/xAI
// generation URLs passed to <video>) can render. The cross-origin <video>
// element is not covered by `default-src` and was being blocked by the
// strict policy.
const MEDIA_SRC_EXTERNAL_HOSTS = ['https:'];
const IMG_SRC_EXTERNAL_HOSTS = ['https:'];

function makeNonce(): string {
  return crypto.randomUUID().replaceAll('-', '');
}

function buildContentSecurityPolicy(nonce: string): string {
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' ${SCRIPT_SRC_EXTERNAL_HOSTS.join(' ')}`.trim(),
    `style-src 'self' 'nonce-${nonce}'`,
    `img-src 'self' data: blob: ${IMG_SRC_EXTERNAL_HOSTS.join(' ')}`.trim(),
    "font-src 'self' data:",
    `connect-src 'self' ${CONNECT_SRC_EXTERNAL_HOSTS.join(' ')}`.trim(),
    `media-src 'self' ${MEDIA_SRC_EXTERNAL_HOSTS.join(' ')}`.trim(),
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
  ].join('; ');
}

export function proxy(request: NextRequest) {
  const nonce = makeNonce();
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set('x-nonce', nonce);
  // Forward the CSP *header* so Next.js attaches it to the rendered HTML
  // response. Next derives inline-script nonces from this header — without
  // forwarding it, the response would only carry the CSP on the outer
  // middleware response (not the rendered page), which lets the framework
  // bootstrap scripts through one nonce but rejects application scripts
  // using a different nonce.
  requestHeaders.set('Content-Security-Policy', buildContentSecurityPolicy(nonce));

  const response = NextResponse.next({
    request: {
      headers: requestHeaders,
    },
  });

  for (const [name, value] of Object.entries(STATIC_SECURITY_HEADERS)) {
    response.headers.set(name, value);
  }
  response.headers.set(
    'Content-Security-Policy',
    buildContentSecurityPolicy(nonce),
  );
  response.headers.set('X-CSP-Nonce', nonce);

  return response;
}
