type HeadersWithSetCookie = Headers & {
  getSetCookie?: () => string[];
  raw?: () => Record<string, string[]>;
};

export function copyResponseHeaders(source: Headers, target: Headers): void {
  source.forEach((value, key) => {
    if (
      key.toLowerCase() === 'set-cookie' ||
      key.toLowerCase() === 'content-encoding'
    ) {
      return;
    }
    target.set(key, value);
  });

  for (const cookie of getSetCookieValues(source)) {
    target.append('Set-Cookie', cookie);
  }
}

function getSetCookieValues(headers: Headers): string[] {
  const extended = headers as HeadersWithSetCookie;

  if (typeof extended.getSetCookie === 'function') {
    const cookies = extended.getSetCookie();
    if (Array.isArray(cookies) && cookies.length > 0) {
      return cookies;
    }
  }

  if (typeof extended.raw === 'function') {
    const rawHeaders = extended.raw();
    const cookies = rawHeaders['set-cookie'];
    if (Array.isArray(cookies) && cookies.length > 0) {
      return cookies;
    }
  }

  const singleCookie = headers.get('set-cookie');
  return singleCookie ? [singleCookie] : [];
}
