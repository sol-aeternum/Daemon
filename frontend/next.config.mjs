import path from 'node:path';
import { fileURLToPath } from 'node:url';

import withSerwistInit from '@serwist/next';

const withSerwist = withSerwistInit({
  swSrc: 'app/sw.ts',
  swDest: 'public/sw.js',
  register: true,
  disable:
    process.env.NODE_ENV !== 'production' || Boolean(process.env.TURBOPACK),
});

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Keep file tracing scoped to this app to avoid monorepo root inference
  // when unrelated lockfiles exist in the parent directory.
  outputFileTracingRoot: path.dirname(fileURLToPath(import.meta.url)),
};

export default withSerwist(nextConfig);
