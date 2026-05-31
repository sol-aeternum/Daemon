/** @type {import('next').NextConfig} */
const withPWA = require('next-pwa')({
  dest: 'public',
  register: true,
  skipWaiting: true,
  disable: process.env.NODE_ENV === 'development',
  runtimeCaching: [
    // Same-origin API routes: NetworkOnly — never cache authenticated responses.
    {
      urlPattern: ({ url }) =>
        url.origin === self.location.origin &&
        url.pathname.startsWith('/api/'),
      handler: 'NetworkOnly',
      options: {
        cacheName: 'api-no-cache',
      },
    },
    // Static assets: Cache-first, long cache
    {
      urlPattern: /\.(?:js|css)$/,
      handler: 'CacheFirst',
      options: {
        cacheName: 'static-resources',
        expiration: {
          maxEntries: 60,
          maxAgeSeconds: 30 * 24 * 60 * 60, // 30 days
        },
      },
    },
    // Images: Stale-while-revalidate
    {
      urlPattern: /\.(?:png|jpg|jpeg|svg|gif|webp|ico)$/,
      handler: 'StaleWhileRevalidate',
      options: {
        cacheName: 'images',
        expiration: {
          maxEntries: 60,
          maxAgeSeconds: 30 * 24 * 60 * 60, // 30 days
        },
      },
    },
    // Fonts: Cache-first
    {
      urlPattern: /\.(?:woff|woff2|eot|ttf|otf)$/,
      handler: 'CacheFirst',
      options: {
        cacheName: 'fonts',
        expiration: {
          maxEntries: 20,
          maxAgeSeconds: 365 * 24 * 60 * 60, // 1 year
        },
      },
    },
    // Catch-all: matches any request that is NOT same-origin /api/.
    // The predicate explicitly excludes same-origin /api/*.
    {
      urlPattern: ({ url }) =>
        !(url.origin === self.location.origin && url.pathname.startsWith('/api/')),
      handler: 'NetworkFirst',
      options: {
        cacheName: 'others',
        expiration: {
          maxEntries: 32,
          maxAgeSeconds: 24 * 60 * 60, // 24 hours
        },
        networkTimeoutSeconds: 10,
      },
    },
  ],
});

const nextConfig = {
  // Keep file tracing scoped to this app to avoid monorepo root inference
  // when unrelated lockfiles exist in the parent directory.
  outputFileTracingRoot: __dirname,
};

module.exports = withPWA(nextConfig);
