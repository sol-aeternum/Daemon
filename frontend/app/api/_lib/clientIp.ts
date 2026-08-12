import { createHmac } from 'node:crypto';
import { isIP } from 'node:net';

let warnedMissingTrustedProxyConfig = false;

function normalizeClientIp(value: string | null): string | null {
  if (!value) return null;
  let trimmed = value.trim().replace(/^"|"$/g, '');
  if (
    !trimmed ||
    trimmed.toLowerCase() === 'unknown' ||
    trimmed.includes(',')
  ) {
    return null;
  }
  if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
    trimmed = trimmed.slice(1, -1);
  }
  if (trimmed.includes(':') && trimmed.includes('.')) {
    const [host, port] = trimmed.split(':');
    if (host && port && /^\d+$/.test(port)) {
      trimmed = host;
    }
  }
  if (isIP(trimmed) !== 0) {
    return trimmed.toLowerCase();
  }
  return null;
}

function trustedProxyIps(): Set<string> {
  return new Set(
    (process.env.DAEMON_TRUSTED_PROXY_IPS ?? '')
      .split(',')
      .map((value) => normalizeClientIp(value))
      .filter((value): value is string => value !== null),
  );
}

function closestUntrustedForwardedFor(
  raw: string | null,
  trustedProxies: Set<string>,
): string | null {
  if (!raw) return null;
  const hops = raw
    .split(',')
    .map((value) => normalizeClientIp(value))
    .filter((value): value is string => value !== null);
  for (let index = hops.length - 1; index >= 0; index -= 1) {
    if (!trustedProxies.has(hops[index])) {
      return hops[index];
    }
  }
  return null;
}

function platformClientIp(req: Request): string | null {
  return (
    normalizeClientIp(req.headers.get('x-vercel-forwarded-for')) ??
    normalizeClientIp(req.headers.get('cf-connecting-ip'))
  );
}

function hasClientIpForwardingHeaders(req: Request): boolean {
  return (
    req.headers.has('x-forwarded-for') ||
    req.headers.has('x-vercel-forwarded-for') ||
    req.headers.has('cf-connecting-ip') ||
    req.headers.has('forwarded')
  );
}

function warnMissingTrustedProxyConfig(): void {
  if (warnedMissingTrustedProxyConfig) return;
  warnedMissingTrustedProxyConfig = true;
  console.warn(
    'Ignoring forwarded client-IP headers because DAEMON_TRUSTED_PROXY_IPS is unset. ' +
      'Configure trusted proxy IPs before using forwarded headers for rate limits.',
  );
}

/**
 * Return the validated client IP for a trusted Next.js proxy hop.
 *
 * Forwarded headers are only consulted when the immediate `x-real-ip`
 * value is itself configured in `DAEMON_TRUSTED_PROXY_IPS`. Otherwise
 * the immediate address is used. This keeps browser-controlled
 * forwarding headers from bypassing IP-scoped rate limits.
 */
export function daemonClientIp(req: Request): string | null {
  const immediateIp = normalizeClientIp(req.headers.get('x-real-ip'));
  const trustedProxies = trustedProxyIps();
  if (trustedProxies.size === 0 && hasClientIpForwardingHeaders(req)) {
    warnMissingTrustedProxyConfig();
  }

  if (!immediateIp) return null;
  if (trustedProxies.size === 0 || !trustedProxies.has(immediateIp)) {
    return immediateIp;
  }

  return (
    closestUntrustedForwardedFor(
      req.headers.get('x-forwarded-for'),
      trustedProxies,
    ) ?? platformClientIp(req)
  );
}

/** Add a short-lived, server-authenticated client-IP assertion for FastAPI. */
export function appendDaemonClientIpHeaders(
  headers: Headers,
  req: Request,
): void {
  const clientIp = daemonClientIp(req);
  const secret = process.env.DAEMON_INTERNAL_PROXY_HMAC_SECRET?.trim();
  if (!clientIp || !secret) return;

  const timestamp = Math.floor(Date.now() / 1000).toString();
  const payload = `v1\n${timestamp}\n${req.method.toUpperCase()}\n${clientIp}`;
  const signature = createHmac('sha256', secret).update(payload).digest('hex');

  headers.set('X-Daemon-Client-IP', clientIp);
  headers.set('X-Daemon-Client-IP-Timestamp', timestamp);
  headers.set('X-Daemon-Client-IP-Signature', signature);
}
