// Offline companion to the manual #765 capture. Commit waits are elapsed
// renderer-main time, including time descheduled; they are not CPU times.
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { gunzipSync } from 'node:zlib';

function unionMs(intervals) {
  let end = -Infinity;
  let us = 0;
  for (const [left, right] of intervals.sort((a, b) => a[0] - b[0])) {
    us += Math.max(0, right - Math.max(left, end));
    end = Math.max(end, right);
  }
  return us / 1000;
}

export function summarizeRailTrace(events) {
  const mark = (name) => {
    const matches = events.filter(
      (e) => e.name === name && e.cat?.split(',').includes('blink.user_timing'),
    );
    if (matches.length !== 1) throw new Error(`Expected one ${name} mark`);
    return matches[0];
  };
  const start = mark('765-start');
  const end = mark('765-end');
  if (start.pid !== end.pid || start.tid !== end.tid || !(start.ts < end.ts)) {
    throw new Error('Mismatched scroll measurement marks');
  }
  const slices = events.filter(
    (e) =>
      e.pid === start.pid &&
      e.tid === start.tid &&
      e.ph === 'X' &&
      e.ts < end.ts &&
      e.ts + e.dur > start.ts,
  );
  const ms = (group, clock = 'ts', duration = 'dur') =>
    unionMs(
      group.map((e) => [
        Math.max(start[clock], e[clock]),
        Math.min(end[clock], e[clock] + e[duration]),
      ]),
    );
  const scrolls = slices.filter((e) => e.name === 'ScrollLayer');
  const rail = scrolls.filter(
    (e) =>
      e.args?.data?.nodeName ===
      "OL class='min-h-0 overflow-y-auto overscroll-y-contain px-2 pb-4'",
  );
  const transcript = scrolls.filter((e) =>
    e.args?.data?.nodeName?.startsWith('MAIN '),
  );
  if (
    !transcript.length ||
    scrolls.length !== rail.length + transcript.length
  ) {
    throw new Error('Missing transcript or unrecognized scrolling element');
  }
  const waits = slices.filter(
    (e) => e.name === 'LayerTreeHost::WaitForCommitCompletion',
  );
  const nested = (parents) =>
    waits.filter((w) =>
      parents.some((p) => p.ts <= w.ts && w.ts + w.dur <= p.ts + p.dur),
    );
  const paint = slices.filter((e) => e.name === 'Paint');
  const hasPaintClock =
    Number.isFinite(start.tts) &&
    Number.isFinite(end.tts) &&
    paint.every((e) => Number.isFinite(e.tts) && Number.isFinite(e.tdur));
  return {
    windowMs: (end.ts - start.ts) / 1000,
    transcriptScrolls: transcript.length,
    railScrolls: rail.length,
    allCommitWaitMs: ms(waits),
    railCommitWaitMs: ms(nested(rail)),
    transcriptCommitWaitMs: ms(nested(transcript)),
    paintWallMs: ms(paint),
    paintThreadMs: hasPaintClock ? ms(paint, 'tts', 'tdur') : null,
  };
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  const files = process.argv.slice(2);
  if (!files.length)
    throw new Error('Pass one or more renderer-trace.json.gz files');
  for (const file of files) {
    const trace = JSON.parse(gunzipSync(readFileSync(file)).toString());
    console.log(
      JSON.stringify({ file, ...summarizeRailTrace(trace.traceEvents) }),
    );
  }
}
