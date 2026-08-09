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
  // Google Identity Services — GIS communicates with this origin during
  // hosted sign-in (AuthLanding.tsx → google.accounts.id.prompt()). Without
  // this exception the GIS iframe prompt and its XHR callbacks are blocked
  // by the strict connect-src policy.
  'https://accounts.google.com',
];

// Hosts allowed in `frame-src` for the GIS iframe-based prompt
// (google.accounts.id.prompt() in AuthLanding.tsx). The hosted Google
// sign-in dialog renders inside an iframe pointed at this origin.
const FRAME_SRC_GIS_HOSTS = ['https://accounts.google.com'];

// `media-src` is required so provider-hosted video/audio (e.g. fal/xAI
// generation URLs passed to <video>) can render. The cross-origin <video>
// element is not covered by `default-src` and was being blocked by the
// strict policy. `blob:` covers authenticated audio that is converted to
// a blob: object URL inside AudioPlaybackProvider / useAuthenticatedImageUrl.
const MEDIA_SRC_EXTERNAL_HOSTS = ['https:'];
const IMG_SRC_EXTERNAL_HOSTS = ['https:'];

// `frame-src` is required so generated / authenticated PDFs (rendered via
// `blob:` and `data:` URLs in `FilePreview.tsx` / `PdfPreview.tsx`) load
// inside an `<iframe>`. Without this, `default-src 'self'` governs frames
// and blocks the inline preview even though the document downloads. The GIS
// host is also allowed so the hosted Google sign-in iframe prompt can
// render inside its own iframe container.
const FRAME_SRC_HOSTS = ["'self'", 'blob:', 'data:', ...FRAME_SRC_GIS_HOSTS];

/**
 * Build the `connect-src` host list. Always includes the static list of
 * runtime adjacencies (ElevenLabs WebSocket, etc.); also includes the
 * configured backend origin from `NEXT_PUBLIC_API_URL` so direct browser
 * hooks (`useConversationHistory` etc.) can reach the daemon backend
 * without going through the same-origin Next API routes. When the env
 * var is unset (the documented local-development fallback — see
 * ``frontend/hooks/useConversationHistory.ts`` and ``frontend/components/Studio*``),
 * include ``http://localhost:8000`` so the dev fallback still passes
 * connect-src evaluation.
 */
function buildConnectSrcHosts(): string[] {
  const hosts = [...CONNECT_SRC_EXTERNAL_HOSTS];
  const apiUrl = process.env.NEXT_PUBLIC_API_URL;
  if (typeof apiUrl === 'string' && apiUrl.trim().length > 0) {
    hosts.push(apiUrl.trim());
  } else {
    // Local-development fallback: when NEXT_PUBLIC_API_URL is unset,
    // hooks like ``useConversationHistory`` and the Studio video
    // generation path explicitly fall back to ``http://localhost:8000``
    // (the default Docker compose backend host). Without this entry,
    // the strict connect-src blocks those cross-origin requests before
    // the same-origin fallback can help. Production deployments must
    // set NEXT_PUBLIC_API_URL so this fallback is never added.
    hosts.push('http://localhost:8000');
  }
  return hosts;
}

function makeNonce(): string {
  return crypto.randomUUID().replaceAll('-', '');
}

function buildContentSecurityPolicy(nonce: string): string {
  const connectSrc = ["'self'", ...buildConnectSrcHosts()].join(' ').trim();
  const mediaSrc = ["'self'", 'blob:', ...MEDIA_SRC_EXTERNAL_HOSTS]
    .join(' ')
    .trim();
  // ``script-src`` requires ``'unsafe-eval'`` in development because
  // Next's webpack dev server emits modules that load via ``eval(...)``.
  // Production builds do not use eval, so the loose keyword is only
  // added when ``NODE_ENV === 'development'`` (the documented
  // ``npm run dev`` command path). This keeps the production CSP
  // strict while allowing the documented local-development workflow
  // to actually render the client bundle (Codex P1 on PR #163).
  const isDevelopment = process.env.NODE_ENV === 'development';
  const scriptSrcParts = [
    "'self'",
    `'nonce-${nonce}'`,
    ...SCRIPT_SRC_EXTERNAL_HOSTS,
  ];
  if (isDevelopment) {
    scriptSrcParts.push("'unsafe-eval'");
  }
  // ``style-src`` includes ``'unsafe-inline'`` because ``docx-preview``
  // (frontend/src/components/previews/DocxPreview.tsx) injects
  // dynamically generated ``<style>`` elements without a nonce, and the
  // generated CSS is what makes the supported DOCX preview render
  // correctly. Nonces authorize whole ``<style>`` tags but cannot be
  // applied to runtime-injected tags from third-party libraries. The
  // strict CSP otherwise blocks those styles and the document /
  // numbering CSS is lost (Codex P2 on PR #163).
  return [
    "default-src 'self'",
    `script-src ${scriptSrcParts.join(' ')}`.trim(),
    `style-src 'self' 'nonce-${nonce}' 'unsafe-inline'`.trim(),
    // `style-src-attr 'unsafe-inline'` is required because React's `style={{...}}`
    // attributes cannot be nonce-tagged (nonces authorize whole `<style>` tags
    // or external stylesheets, not inline attribute values). The repository
    // uses these attributes for behavior-critical values such as the
    // conversation menu's `top`/`left`, video and council progress widths,
    // preview colors, and input sizing — without this, browsers enforcing
    // strict CSP silently strip those values and leave controls misplaced.
    "style-src-attr 'unsafe-inline'",
    `img-src 'self' data: blob: ${IMG_SRC_EXTERNAL_HOSTS.join(' ')}`.trim(),
    "font-src 'self' data:",
    `connect-src ${connectSrc}`,
    `media-src ${mediaSrc}`,
    `frame-src ${FRAME_SRC_HOSTS.join(' ')}`.trim(),
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
  requestHeaders.set(
    'Content-Security-Policy',
    buildContentSecurityPolicy(nonce),
  );

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
