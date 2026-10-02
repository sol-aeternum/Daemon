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
