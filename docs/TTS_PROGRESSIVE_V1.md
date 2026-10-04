# Progressive speech v1

## Authority and release boundary

The owner approved D1–D6 of the 4 October 2026 v1.1 design, then authorized
implementation and a review-ready PR for #433. This does **not** authorize merge,
deployment, new dependencies, model/voice changes or larger resource limits.
The owner subsequently selected **keep it gated**: buffered playback remains the
default until separately approved device/listening qualification. Backend and
client release constants are reviewed source gates, not entitlements or new
environment variables. Authenticated direct v1 requests are for qualification;
capabilities do not advertise progressive selection while gated.

Existing [speech architecture](TTS_ARCHITECTURE.md) and buffered MP3/WAV/Opus
contracts remain intact. This is additive delivery for completed assistant
messages, not LLM-token auto-play, durable-task continuity or playback persistence.

## Wire contract

- Public backend: authenticated `GET /tts/capabilities`, `POST /tts/stream/v1`.
  Same-origin bridge: `/api/tts/capabilities`, `/api/tts/stream/v1`.
  Private runtime: `POST /synthesize/stream/v1`.
- Success MIME: `application/vnd.daemon.speech-stream;version=1`, magic `DSP1`.
  Frames: unsigned byte type, big-endian unsigned 32-bit payload length, payload.
- Type0 first/once: flat JSON metadata (`version`, opaque `stream_id`, canonical
  `provider`, `model`, `voice`, effective `speed`, `format`, `mime`, `sample_rate`,
  `rendering`, `cached`). Format MP3, media MIME `audio/mpeg`, rate24000 and
  rendering `speech-mp3-progressive-v1` must match the authenticated capability
  snapshot and captured request. All three version carriers must agree.
- Type1: big-endian unsigned 32-bit sequence number plus nonempty consecutive
  slices of one continuously encoded MP3; sequence starts0, no gaps/duplicates.
- Type2 success: flat JSON `frames`, `bytes`, `source_seconds`, `encoded_seconds`,
  `synthesis_seconds`, nullable protected `audio_path`, boolean `cache_available`.
  Counts cover only MP3 bytes/audio frames, not sequence/framing/control overhead.
- Type3 failure: flat JSON allowlisted `code` only; no payload/provider diagnostics.
- Type4: empty heartbeat, bounded cadence; neither completion nor deadline renewal.

Arbitrary HTTP boundaries are tolerated; unknown/duplicate/out-of-order/truncated
frames, nonfinite numbers, invalid identity and trailing data are rejected.
Success requires matching complete totals **and clean HTTP body EOF**. A playable
prefix, HTTP200, bare EOF or reset after a terminal before EOF is failure. No
automatic replay follows an ambiguously started POST.

Limits derive from `orchestrator/speech/stream_protocol.py` and the authenticated
capability response: raw3000 code points and32KiB input, encoded16,000,000 bytes,
generated source≤300 seconds. MP3-only padding allowance≤0.15 seconds is conditional
on pinned-codec qualification; it is not additional generated speech. Frame
payload≤65536 bytes including sequence, control≤4096 bytes, audio frames≤16384,
heartbeats≤32 and total wire≤20,000,000 bytes. Parser/append/producer memory is
bounded; no full utterance PCM buffer is introduced for streaming.

## Runtime and authority ownership

Buffered and progressive synthesis share the existing single native slot and
loaded offline model, voice and CPU profile. Current240-code-point chunking is
preserved. One encoder spans all chunks and flushes once. Disconnect, Stop and
deadline signal cancellation; the slot remains busy until native/encoder work
actually exits, never merely because an HTTP waiter was cancelled.

Absolute runtime/API deadlines cover synthesis, blocked queues/writes, encoding,
EOF and publication waits. Five-second idle heartbeats and15-second transport
idle checks do not extend those deadlines. Continuous drain stalls fail after
ten seconds. HTTP closure does not wait indefinitely for an uninterruptible native
chunk or an already-started atomic writer; those tails remain explicitly owned.

The original authenticated user/device/session and bearer hash are immutable.
Speech-private read-only checks reuse session/device revocation and DB-expiry
predicates, with conservative query-start monotonic expiry, no last-seen writes
and no credential refresh. Five-second rechecks have a two-second total budget,
at most one transient read retry, payload gating while pending, and fail-closed
expiry/revocation/unavailability. Authority loss independently interrupts blocked
I/O. Successor tokens cannot extend an older stream's authority. No POST retry.

Each outbound checkpoint also enforces the five-second refresh deadline, so a
delayed monitor cannot authorize a frame from a stale snapshot after an event-loop
stall. Concurrent overdue checks share one read; forced prepublication checks
remain unconditional. Buffered cache lookups and writes run off-loop with owned
thread tails through cancellation, keeping the shared filesystem lock from
blocking the authority monitor.

## Cache, proxy and player

Validated empty owner namespaces are reclaimed under the cache root lock after
journal validation and recovery. Live reservation owners, unknown contents and
non-namespace directories are preserved; directory identity is rechecked without
following symlinks. This bounds normal owner churn without widening scan/file
budgets. An already-over-limit or malformed cache still fails closed for operator
diagnosis; this is not an unbounded cleanup or migration of existing disk state.

Rendering identity differs from buffered `speech-v1`. Partial audio and private
reservation metadata live outside served owner directories. All buffered and
progressive writers share64MiB/128 physical-file accounting, including bounded
control metadata and temporary peaks. Progressive reservation uses free capacity
after expiry pruning, never speculative eviction of a valid completed clip.
No space or optional cache failure means valid uncached playback, not more memory
or a discarded live stream. Existing completed files/identities remain compatible.

Per-reservation OS locks and root-lock accounting distinguish active from abandoned
staging; no PID/age-only purge. Complete private manifest/inode identity precedes
atomic MP3 publication. Runtime terminal+EOF and authority are checked first.
Cache hits pin a validated owner file and use the same framing, no second download.
`cache:false` bypasses streaming lookup/publication; old buffered semantics remain.
Returned paths are protected and momentary, not public or durable availability.

Recovery is qualified for process crashes with complete journals, not power loss.
An empty/partial lease or manifest disables caching and logs the stable payload-free
`speech_cache_state_unavailable` code once per process. Unjournalled buffered
temporary files remain counted and preserved; their ownership cannot be inferred
from age/name alone. An operator must diagnose/remediate those entries with all
writers stopped before restarting; no automatic broad purge or valid-clip deletion
is permitted. This conservative failure mode can reduce buffered-cache availability
after an incomplete disk journal, while valid uncached progressive playback remains
available. It is not a claim of complete power-loss recovery.

Proxy selection uses safe preflight/configuration before one synthesis POST; it
never switches address/transport afterward. Streaming bodies are not converted to
JSON/blob/arrayBuffer. Cancellation and bounded backpressure propagate through
each hop. Responses are private/no-store/no-transform without compression or
service-worker persistence; `X-Accel-Buffering:no` is advisory, not proof.

The existing attached provider-owned media and bottom player are retained.
Generation and playback states are independent: Pause retains position/media and
allows bounded generation to finish; completion never silently resumes it. Seek
uses actual buffered∩seekable ranges, not future audio or estimated percentages.
After complete+EOF, drain appends then end MediaSource; native ended is distinct
from generation success. Post-prefix provider/decode/append failure visibly
interrupts playback with explicit user retry. Stop/replacement/auth/content/scope
changes invalidate ownership before cleanup. No reload/navigation persistence.

## Qualification and PR evidence

The pinned PyAV16.1.0 codec was independently exercised in the existing isolated
image with network disabled,2CPU/1536MiB limits and current source mounted read-only.
From one sample through300 seconds of fictional PCM, decoded duration matched
counted emitted MPEG frames. Padding ranged0.048–0.072 seconds; the300-second case
encoded300.048 seconds. Real offline Kokoro warm qualification and a300-code-point
fictional request also completed. Its first encoded bytes took2.26 seconds in
that one run; a worker's separate279-code-point run took4.43 seconds. Neither is
a p95, physical first-audible or sustained-load result.

Current-source controller smoke in headless Linux Brave additionally confirmed
clean EOF/encoded duration, native available ranges during generation, attached
Pause/seek/Resume and cancellation using a fictional ten-second tone. That harness
does not exercise the entire authenticated Next/backend served path, Android,
installed Plasma or physical listening. It does not clear the release gate.

The earlier fictional transport spike establishes only limited Linux headless
Brave viability, not Android, installed Plasma, physical sound, Kokoro startup,
sustained load or a warm p95 guarantee. Warm first audible speech p95≤3 seconds
remains a qualification target. Shorter initial chunks require a separate rendering
and quality/timing decision if the current chunker misses it.

Required implementation evidence includes malformed framing/limits/truncation,
auth expiry/revocation during blocked work, native cancellation/slot races, cache
process-crash/recovery/inode/symlink/capacity/owner isolation, no POST replay,
proxy cancellation/backpressure, early/late Pause through EOF, available-range seek
and Stop/A→B during active native append. Automated tests do not substitute for
real Android/installed-Plasma listening acceptance. The PR must include final-state
project gates and independent review; merge/deployment require separate approval.
