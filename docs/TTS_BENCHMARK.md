# TTS MVP measurement — 2 October 2026

Real image/model, no mocks, no TTS key, network disabled. Image ID
`sha256:c80ff7fd0eb04ebd58c45686c6f47535ff048a71afd1bff443c2dd5b4c449928`.
Raw JSON was retained locally at `/tmp/opencode/daemon-tts-benchmark.json`;
the reproducible runner is `tts/benchmark.py`, command in TTS_ARCHITECTURE.md.

Environment: x86_64 Linux, Intel i7-1165G7 (4 physical cores/8 threads), 15 GiB
host RAM, existing host workload and significant pre-existing swap usage.
Container capped at **2 CPUs / 1536 MiB**, one model, FP32 ONNX, non-root,
read-only filesystem, private tmpfs, no network. Not a dedicated production host.

Cold **process/model-load/warm-synthesis to HTTP readiness: 2.186 seconds**.
This is a new container/process with warm host filesystem page cache; not a
machine boot or disk-cold measurement. A separate offline model-load smoke was
3.210 seconds with 487 MiB peak RSS. Full HTTP benchmark peak RSS was
**730 MiB** (747748 KiB), including decoding the outputs for verification.
Actual Docker-reported uncompressed image size: **1,370,068,761 bytes**;
model assets: **353,719,767 bytes** (338 MiB), locked installed environment
approximately 271 MiB. Image size includes base/tool/layer overhead; not the
compressed registry transfer size. Build-time downloads never occur at readiness
or on first request.

| Input | Format | Characters | Synthesis (s) | Time to playable (s) | Audio duration (s) | RTF |
|---|---|---:|---:|---:|---:|---:|
| Short conversational response | MP3 | 87 | 4.575 | 4.608 | 6.485 | 0.706 |
| Short conversational response | WAV | 87 | 3.706 | 3.708 | 6.485 | 0.572 |
| Short conversational response | Opus | 87 | 3.943 | 3.944 | 6.485 | 0.608 |
| Moderate response | MP3 | 657 | 26.419 | 26.422 | 38.394 | 0.688 |
| Moderate response | WAV | 657 | 25.962 | 25.965 | 38.394 | 0.676 |
| Moderate response | Opus | 657 | 27.857 | 27.859 | 38.394 | 0.726 |

RTF = synthesis wall time / audio duration; lower than 1 is faster than playback.
All outputs were decoded and verified non-silent, with decoded duration matching
reported duration. Time to playable is the complete buffered HTTP audio payload;
it is **not streaming time to first playable chunk** and excludes public API,
browser/network overhead. First HTTP response was within about 2 ms of complete
payload arrival. A one-machine run does not establish sustained capacity or a
production latency guarantee. No human listening/quality adjudication was done.

The model is operationally small and CPU-capable, but buffered moderate responses
wait roughly 26–28 seconds here. The next latency improvement should be a bounded
streaming-capable runtime adapter/transport or a faster worker, not pretending
the buffered result starts playing before synthesis finishes. Keep the existing
buffered public API while adding streaming as an optional capability.

## Final-state verification and release boundary

The final image, including updated licensing notices, was subsequently built as
`daemon-tts:final-20261002`, image ID
`sha256:2f0442cb05066f3de214825a588bb23f1f0e51d39c40897b1427d83286e93a5a`,
1,370,068,963 bytes. Its actual Compose TTS service configuration passed
health/readiness, internal-network/no-published-port, CPU/RAM/read-only checks
and real non-silent decoded WAV synthesis in an isolated credential-free
project. The disposable service/network were removed. Timings above remain
measurements of the earlier image; they were not remeasured on this final image.

The real public `/tts` → private runtime → authenticated-owner audio retrieval
and cache smoke passed with fictional authentication/admission fixtures, not a
live user session. All six frontend source gates passed in a clean source
snapshot (486 tests); the final proxy also passed direct header/abort checks.
Existing checkout-generated `.next*` artifacts were preserved rather than
deleted. Speech regression tests passed, but the original dirty-checkout backend gate run reported
204 failures, 4689 passes and 124 skips. Individual failures have not all been
proven pre-existing. That checkout was **partially verified, not release-ready**;
it is not the clean current-main PR branch. Final clean-branch gate evidence is
recorded separately in the PR; these historical failures are not its gate results.
No live-stack deployment, restart, real-browser playback acceptance or human
listening adjudication was performed.

## CPU tuning investigation — 3 October 2026

The owner subsequently reported longer replies preparing until `speech_timeout`
([#433](https://github.com/sol-aeternum/Daemon/issues/433)); short playback success
did not verify long-message recovery. On the exact final image above, an offline
fictional 2,800-character input reproduced HTTP 504 at 120.020 seconds. A
650-character control took 61.835 seconds, illustrating shared-host variability.

Same-image experiments kept the FP32 graph, bundled `af_heart` voice, speed 1,
MP3, two CPUs, 1536 MiB, sequential execution and existing deadlines. The only
candidate change disabled ONNX intra/inter-op worker spinning. Balanced
confirmation reversed the screen order, discarded the same 80-character shape
warmup per arm and used identical input hashes:

| Input | Current spinning settings (s) | No spinning (s) | Reduction |
|---|---:|---:|---:|
| 650 characters, two runs per arm (median) | 24.222 | 18.361 | 24.2% |
| 2,800 characters, one run per arm | 109.219 | 75.898 | 30.5% |

All confirmation outputs decoded finite and non-silent, with decoded duration
within 50 ms of the reported duration. Both long outputs were about 169.106
seconds. The baseline long request had 1,045 throttled CPU periods versus five
without spinning; these counts support reduced quota contention, not an exact
attribution of wall-time savings. One inference thread was slower and rejected.

An owner-approved isolated upstream INT8 screen was also rejected on this
hardware/runtime: with identical no-spinning settings, median 650-character time
was 90.164 seconds versus tuned FP32's 21.057 seconds, and 2,800 characters timed
out at 120.033 seconds. Tuned FP32's additional 3,000-character control completed
in 97.434 seconds. No model, voice, precision, dependency, resource or deadline
switch is part of the CPU tuning change.

These are buffered private HTTP measurements, not browser startup, sustained-load
capacity or a guarantee that every accepted reply finishes within the deadline.
Only one long replicate per arm was used; host variability, slower speech and
content shape remain relevant. Decoder validity does not establish equivalent
pronunciation or perceptual quality. The change remains FP32; any future precision
switch needs distinct model/cache identity and separate qualification. Updated
runtime source or an isolated successful benchmark does not establish local
deployment or owner acceptance. The issue remains open until its remaining
long-message usability requirements are resolved.
