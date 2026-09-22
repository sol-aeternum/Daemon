# UI design tokens

The token values live in `frontend/app/globals.css`; named Tailwind utilities
are mapped in `frontend/tailwind.config.ts`. This implements issue #34.

- Use `--color-text-primary`/`secondary`/`muted` on app surfaces. Use
  `--color-text-inverse` only on inverse surfaces: it changes with the theme.
- Use `--color-text-on-accent` for filled accent controls, `on-status` for
  error/stop fills, and `on-warning` for warning fills.
- Media stays dark in both themes. `bg-media` and `text-on-media` support
  opacity modifiers such as `/50`; document paper and QR quiet zones have
  separate fixed light background tokens.
- `text-xs` is the smallest label size (12px at the default root size).
  Use the font-size scale instead of arbitrary pixel classes.
- `min-h-touch` and `min-w-touch` reference `--touch-min` (44px).
- Use scale utilities for ordinary spacing. Component geometry that cannot
  use the scale has a named token, including sidebar width, message widths,
  preview heights, and the Studio and skill-editor grid columns.
- Safe-area utilities retain the device inset while respecting spacing tokens.

`npm run lint` rejects literal Tailwind values, black/white utilities, and the
retired `daemon-*` color namespace in app and component source, including
conditional templates and shared class maps. Explicit `var(--token)` values
and named utilities remain supported. This policy does not inspect generated
document contents or dynamic inline measurements.

`npm run test:browser -- --project=design-tokens` checks eight pages in both
themes, including label sizes, touch sizing, overflow, and screenshot baselines.
Browser output is retained in the CI `browser-regressions` artifact. Review
intentional visual changes before updating baselines with
`npm run test:browser -- --project=design-tokens --update-snapshots` on Linux.
