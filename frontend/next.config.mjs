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
  // Keep idle development chrome out of application/browser-test hit targets.
  // Build/runtime error UI remains enabled. The browser regression exercises
  // the uncaught runtime-error path, not compilation failures.
  devIndicators: false,
  // Keep file tracing scoped to this app to avoid monorepo root inference
  // when unrelated lockfiles exist in the parent directory.
  outputFileTracingRoot: path.dirname(fileURLToPath(import.meta.url)),
};

export default withSerwist(nextConfig);
