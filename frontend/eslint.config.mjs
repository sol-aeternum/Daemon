import { defineConfig } from 'eslint/config';
import nextVitals from 'eslint-config-next/core-web-vitals';
import prettier from 'eslint-config-prettier/flat';
import designTokens from './eslint-rules/design-tokens.mjs';

export default defineConfig([
  ...nextVitals,
  prettier,
  {
    files: [
      'app/**/*.{js,jsx,ts,tsx}',
      'components/**/*.{js,jsx,ts,tsx}',
      'src/components/**/*.{js,jsx,ts,tsx}',
    ],
    ignores: ['**/*.test.*'],
    plugins: { 'design-tokens': designTokens },
    rules: { 'design-tokens/no-literal-values': 'error' },
  },
  {
    ignores: [
      '**/.next/**',
      '**/.next_new/**',
      'out/**',
      'build/**',
      'coverage/**',
      'next-env.d.ts',
    ],
  },
]);
