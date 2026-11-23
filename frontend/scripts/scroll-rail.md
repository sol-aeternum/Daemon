# Does the Contents rail's auto-scroll cost a frame?

The [#765 trace analysis](https://github.com/sol-aeternum/Daemon/issues/765#issuecomment-7095286691) found a Chromium `LayerTreeHost::WaitForCommitCompletion` interval nested inside the desktop Contents list's `scrollTop` write. The median was about 50 ms per one-second window under fixed CPU contention, versus zero on the quiet host. That was **elapsed renderer-main time**, not 50 ms of CPU. The four-browser failure had no equivalent renderer trace.

This manual experiment compares the same production build with the rail write forwarded to Chromium or suppressed on **one target page**. Its three peers always scroll the original chat. The transcript, active-prompt selection, highlighting, geometry reads and React effects stay live in both arms. Suppressing auto-reveal is an experimental control, not a usable UI fix.

## Capture on an otherwise idle Linux machine

Use the locked frontend install, a dedicated Playwright browser cache, and the same CPU/power settings for the whole run. Record the git SHA, production build ID (`.next/BUILD_ID`) and CPU/load alongside the output. Do not run the comparison during builds, CI or unrelated browser jobs. The three intentionally competing browsers are part of the experiment.

From `frontend/`, build once for `http://127.0.0.1:3181`. Use the actual lock-matched Chromium headless shell verified by the benchmark preflight, not a cache directory name:

```sh
npm ci
export PLAYWRIGHT_BROWSERS_PATH=/path/to/private/playwright-cache
./node_modules/.bin/playwright install chromium --only-shell
CI=1 NEXT_PUBLIC_API_URL=http://127.0.0.1:3181 npm run build
./node_modules/.bin/playwright test --config=playwright.rail765.config.ts \
  --output=/path/to/new/rail-capture
```

The config starts the built app on port 3181. Use a new output directory: Playwright cleans it at startup. The one manual case runs A/B then B/A with fresh pages; it writes `window.json` (frame timestamps, p95 gap, geometry, common-clock peer overlap and setter counts) and `renderer-trace.json.gz` for each window. It is excluded from the ordinary browser inventory. Use `--list` for a discovery check without launching browsers.

The trace marks bound the same one-second rAF loop as qualification, not a later settle period. `results[0].measuredWrites` counts attempts inside that loop; `after.writes` also includes attempts outside it. Compare both before attributing a late rail update to the measured frames.

Keep the raw traces with the window measurements, including slow samples. To print the renderer-main summary from one or more capture files:

```sh
node scripts/scroll-rail-trace.mjs /path/to/window/renderer-trace.json.gz
```

## Decide what the comparison shows

1. Compare baseline/variant in both orders. Check that the pages were visible, all four actual scroll windows overlapped, the target reached the same distance and final active prompt, and its setter was attempted. The variant's rail offset is _supposed_ to differ. The baseline should reproduce both the slowdown and the nested rail wait. Otherwise these captures cannot explain the original failure.
2. Read `railCommitWaitMs` **and** `allCommitWaitMs`: a missing rail wait that merely moved elsewhere on the main thread is not saved time. Compare per-frame intervals (especially p95), frame count, elapsed duration and whole-window wait, not just an FPS pass or one slowest frame. `paintWallMs` counts nested Paint intervals once and includes descheduling. Do not add it to the wait total as if the two were disjoint CPU costs.
3. Only a repeatable reduction in whole-window cost **and** better frame delivery over the same trajectory supports pursuing a behavior-preserving rail fix. An inconclusive result stays inconclusive. No timing result from this diagnostic changes a qualification gate.
