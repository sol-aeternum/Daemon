import assert from 'node:assert/strict';
import test from 'node:test';
import { summarizeRailTrace } from './scroll-rail-trace.mjs';

const event = (name, ts, dur, rest = {}) => ({
  name,
  ts,
  dur,
  ph: 'X',
  pid: 1,
  tid: 1,
  ...rest,
});
const marks = ['765-start', '765-end'].map((name, i) =>
  event(name, 1000 + i * 1000, 0, { cat: 'blink.user_timing', ph: 'I' }),
);
const scroll = (tag, ts, dur) =>
  event('ScrollLayer', ts, dur, { args: { data: { nodeName: tag } } });
const wait = 'LayerTreeHost::WaitForCommitCompletion';
const main = scroll("MAIN class='transcript'", 1050, 20);
const rail = scroll(
  "OL class='min-h-0 overflow-y-auto overscroll-y-contain px-2 pb-4'",
  1200,
  200,
);

test('a wait displaced from the rail still counts against the whole window', () => {
  const events = [
    ...marks,
    main,
    rail,
    event(wait, 1230, 100),
    event(wait, 1950, 200),
    event(wait, 1400, 500, { tid: 2 }),
    event('Paint', 900, 300),
    event('Paint', 1050, 100),
  ];
  const base = summarizeRailTrace(events);
  assert.equal(base.allCommitWaitMs, 0.15);
  assert.equal(base.railCommitWaitMs, 0.1);
  assert.equal(base.paintWallMs, 0.2);
  const variant = summarizeRailTrace(events.filter((e) => e !== rail));
  assert.equal(variant.railScrolls, 0);
  assert.equal(variant.railCommitWaitMs, 0);
  assert.equal(variant.allCommitWaitMs, base.allCommitWaitMs);
});

test('unmarked or unexpected scroll paths are not zero-cost results', () => {
  assert.throws(() => summarizeRailTrace([marks[0], main]), /765-end/);
  assert.throws(
    () => summarizeRailTrace([...marks, main, scroll('OL', 1200, 200)]),
    /unrecognized/,
  );
});
