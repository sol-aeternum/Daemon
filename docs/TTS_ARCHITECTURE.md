# Self-hosted speech rendering

## Local rollout evidence boundary

The owner authorized completing the local rollout after the frontend-only
deployment. PR #409 preserves the newer deployed routing configuration and
full-history sidebar title search alongside the speech lifecycle fixes. The
private runtime was staged successfully before an interrupted rollout; staging
alone is not backend/worker activation or live-account acceptance. Final local
activation, source/image identities, rollback references and checks are recorded
in the dated PR rollout report rather than inferred from this architecture spec.
Real-account read-aloud and human listening acceptance are separate from real
private-provider synthesis and fictional desktop/mobile browser fixtures.

## Boundary and default

The approved MVP is ordinary authenticated read-aloud, not sound-effects
generation. The normal Compose stack includes a private `tts` service. Its
default is Kokoro-82M v1.0 (Apache-2.0 weights), using kokoro-onnx 0.6.1 (MIT)
and ONNX Runtime on CPU. `daemon-default` maps to `af_heart` only inside
`tts/runtime.py`. The browser never runs the canonical model or receives
provider credentials. This follows DEC11's replaceable-model principle and
the applicable bounded-execution/privacy principles; it does not enable DEC12
R routes, STT, sound effects, or premium external providers.

```text
message play button → POST /api/tts → authenticated POST /tts
  → speech service → SpeechProvider → private HTTP runtime
  → bounded owner-scoped audio cache → authenticated /generated-audio/{file}
```

`orchestrator/speech/contracts.py` owns the narrow request, audio metadata,
capabilities and async synthesize/health protocol. `provider.py` owns internal
HTTP translation; route code knows neither Kokoro files nor its physical
voices. The dedicated service isolates native dependencies and one loaded
model from API replicas, allowing independent CPU/GPU placement later.

## Packaging and readiness

`tts/pyproject.toml` and `tts/uv.lock` are a separate locked environment. The
image pins base-image digests and downloads the model/voices during **build**,
verifying SHA256 against `tts/download_assets.py`. Assets live in `/opt/models`;
there is no runtime downloader, hub loader or user-controlled file selection.
Missing assets fail startup. Runtime uses a read-only filesystem, non-root
user, dropped capabilities, bounded private tmpfs, two CPU cores and 1536 MiB.
Phonemizer loads a temporary copy of its eSpeak shared library, so that tmpfs
must allow executable mappings; no shell is invoked. ONNX telemetry is disabled.

The service has no published host port and only joins the internal
`speech-network`. Only the API is connected to that network. `/live` answers
process liveness; `/ready` reports model/voice readiness and capabilities only
after actual warm phonemization, synthesis and all three encoders succeed.
Compose blocks API startup until the service is healthy. Runtime network
egress is unavailable; speech therefore cannot trigger a first-use download.
The image includes model/runtime licensing notices; preserve them on distribution.
The model and ONNX adapter permit commercial use. Phonemizer/eSpeak are GPL:
third-party image redistribution also needs license/source-availability compliance;
Apache-licensed model weights alone do not waive runtime obligations.

## Run and configuration

Configure the usual Daemon database/auth secrets; no speech API key is needed.

```sh
docker compose build tts backend frontend
docker compose up -d
docker compose logs tts
# Using an existing Daemon access token:
curl -H "Authorization: Bearer $DAEMON_ACCESS_TOKEN" http://localhost:8000/tts/health
curl -H "Authorization: Bearer $DAEMON_ACCESS_TOKEN" -H 'Content-Type: application/json' \
  -d '{"text":"Hello from Daemon","format":"wav"}' http://localhost:8000/tts
```

Retrieve the returned `audio_path` with the same authenticated account. Existing
web controls do this automatically and play a protected audio blob.
Native development can use a container for the speech runtime and the normal
API development runner, with a deliberately loopback-only debug port and
`TTS_SERVICE_URL` pointing to it. Do not publish that port in production.

Server configuration is declared in `orchestrator/config.py` and documented once
in `.env.example`: `TTS_PROVIDER`, `TTS_MODEL`, `TTS_SERVICE_URL`, and
`TTS_TIMEOUT_SECONDS`. Defaults select the bundled service. Provider/model
identity must match its readiness and audio-response metadata; a mismatched
configuration fails closed. No automatic paid/vendor fallback exists.

## Public compatibility and transport

`POST /tts` retains `{text, voice?, model?, speed?, format?, cache?}` and returns
`{audio_path,cached,model,voice,format}`. Accepted formats remain MP3, WAV and
Opus (Ogg container). The obsolete client `model` field is accepted but ignored:
model choice belongs to the server. Known legacy voice IDs/names map centrally
to `daemon-default`; unknown names are rejected. Replacement voice is not a
clone of the previous vendor voice. Speed is synthesized once on the server,
not applied again during browser playback. `cache:false` forces fresh synthesis;
the response still uses a bounded temporary artifact because the API returns
an audio path rather than audio bytes.

The MVP buffers synthesis and playback. The previous vendor WebSocket components
were unused in the current chat and depended on denied token routes; they are
removed. Per-message playback is restored. Auto-play of streaming LLM output
is deliberately disabled, with saved preference retained. `/audio/token` stays
denied for old clients; clients relying on direct ElevenLabs WebSockets must use
Daemon's existing `/tts` contract instead. STT/vendor sound-effects code and
optional legacy key remain retired and are not needed for ordinary operation.

### Buffered client ownership

One shared playback controller owns synthesis, authenticated audio download and
the media element under a request generation, stable message ID and committed
conversation/authentication scope. Selecting another message invalidates the old
owner before aborting, pausing or revoking its object URL. Every async continuation,
media event and play rejection checks ownership and scope; obsolete callbacks
cannot replace or stop the newer selection. Stop remains available throughout
synthesis, download, playback startup and playback. Authentication invalidation
also cancels imperatively, before React commits the new scope. A retired sign-in's
rendered message cannot start a request during that transition.

Owner unmount, content change, loss of completion eligibility and conversation
changes cancel the captured request. Cleanup is request-qualified so a different
control cannot release its successor. The scoped provider is never keyed/remounted
on navigation: streaming and draft state in its chat descendants stays intact.
Only completed assistant content is eligible for this buffered MVP. The client
counts raw Unicode code points against the API's 3,000-character limit and displays
a disabled control with an explanation above it; it neither truncates nor issues
sequential client chunks. POST validation, download/authentication, decoder and
playback failures are visible, accessible and request-scoped, with retry.

Each media element is attached to a hidden, stable provider-owned DOM host before
`play()`. This avoids KDE Plasma Integration's detached-player discovery hook,
which briefly inserts/removes media and interrupts its pending play promise
([#417](https://github.com/sol-aeternum/Daemon/issues/417)). Retirement first
invalidates ownership, clears handlers, pauses and resets the media resource to
cancel queued native play events, then removes only that element and revokes its
blob URL. The extension can remain enabled; no global media override, hidden error
suppression or automatic synthesis retry is used.

The web `/api/tts` bridge converts CommonMark/GFM to readable content using the
same installed parser as the response UI, on the server rather than adding a
client parser bundle. It drops formatting delimiters, list markers and link
destinations, reads labels/image alt text, and separates table cells/rows. At the
owner's request, inline/fenced code, literal operators, underscores and raw HTML
text are retained, not interpreted or executed. Displayed Markdown is unchanged.
The raw 3,000-code-point bound is checked before parsing; cleaned text still goes
through the backend's existing validation/admission/cache path. Other API clients
continue to supply their intended speech text to `/tts`; its contract is unchanged.

`tts_settings` remains the local preference key. A dedicated subscribed store
validates partial/legacy/corrupt values against defaults and updates mounted
consumers after same-tab setters, cross-tab storage changes and storage clears.
Normalization never writes on read. Each request captures the latest validated
voice, finite speed and format once; changing preferences does not alter an
in-flight request. Browser playback rate stays at one: speed is applied on the
server only. This changes neither backend admission nor the public API contract.

## Resource protection, cancellation, storage and accounting

The API enforces a 32 KiB body limit, 3000-character text maximum, nonempty text,
finite speed 0.5–2.0, format/voice allowlists, control-character rejection and
extreme repetition protection. It never silently truncates normal text. The
runtime chunks internally at sentence/word boundaries with a 240-character
native-call limit. Audio is capped at 300 seconds and 16 MB. The private service
revalidates input and limits HTTP concurrency. One process loads one model;
one synthesis runs at a time. Overload returns 429 with no unbounded queue.
The API admits at most four provider calls per process; the runtime's single
slot bounds native compute across API replicas.

Redis rate admission uses separate `speech:tts` account keys (12 requests/minute)
and fails closed on unavailable Redis in **all** deployment modes. Speech needs
no LLM funding balance or `audio_generation` premium grant. This is an explicit,
task-approved self-hosted read-aloud boundary; it is NOT a zero-cost exemption
for external tools. Operator CPU/RAM cost exists. Paid media, external service
approval, inference policy and funded-reservation semantics are unchanged.

API request disconnect/abort closes the provider request. Runtime disconnect
and its 120-second deadline set a cancellation signal. It stops between native
chunks; an already-running native inference cannot be preempted. The synthesis
slot stays occupied until that work actually finishes, including after an HTTP
timeout. A model failure produces sanitized errors, never text, paths or stack
traces. `TTS_TIMEOUT_SECONDS` is the API's whole-call deadline (default 125s).
Raising that API deadline does not raise the runtime's fixed 120-second ceiling.
Cancellation is not rollback of a completed artifact write: an already-started
atomic cache write may finish after disconnect, within the same owner/TTL/capacity
bounds. Runtime audio-duration rejection remains a sanitized HTTP 413 at
the public API rather than being reported as temporary provider unavailability.

Only new audio is stored under `data/tts_cache/self-hosted`, using existing
authenticated-owner namespace and atomic-file helpers. Cross-process cache
insertion is serialized with a filesystem lock. Capacity is 64 MiB/128 files
globally with one-hour validity; expired/oldest entries are removed during
insertion and expired entries are not served. Existing legacy artifacts are not
deleted or reused as new speech. Cache eviction can require later re-synthesis.
The cache is temporary, not durable workspace storage or an account export.

Payload-free `speech_usage` logs record provider/model, characters, audio
duration, synthesis/wall time and outcome; cache hits have a separate event.
They do not debit or emit LLM-token usage. Durable speech billing/allowances are
not implemented. Future allowances should use audio/voice minutes, not token
equivalents. Ship structured log collection if persistent telemetry is needed.

## Tests and benchmark

`tests/test_speech.py`, `tests/test_speech_runtime.py` and the existing artifact/
format regressions cover the provider contract, replacement provider, API,
identity, invalid input, failures/deadlines, cancellation-safe slot, readiness,
cache bounds and ownership. Frontend tests mount the real shared playback
provider with deferred fetch responses, independently controlled Audio elements
and object URLs. They exercise competing messages, every active phase, stale
callbacks, navigation/auth invalidation, owner cleanup, visible failures/retry,
raw Unicode limits and validated reactive preferences. These are controlled
client integration tests, not real listening acceptance; they require no paid
service or model download.

Real offline smoke and repeatable HTTP benchmark (actual image/model/encoders):

```sh
docker build -f tts/Dockerfile -t daemon-tts:smoke .
docker run --rm --network none --cpus 2 --memory 1536m --read-only \
  --tmpfs /tmp:size=64m,mode=1777,exec --cap-drop ALL \
  --security-opt no-new-privileges daemon-tts:smoke python -m tts.benchmark
```

It measures cold process-to-ready, buffered time to playable audio, synthesis,
audio duration, RTF, peak RSS, and decodes non-silent output for short and moderate
responses in every advertised format. Results and host limitations are recorded
in `docs/TTS_BENCHMARK.md`; single-machine measurements are not production capacity.

## Replacing the model

For a stronger model, first implement a runtime adapter on a separately sized
CPU/GPU host. It must load pinned assets before readiness and honor the same
private HTTP/metadata contract, stable voice mapping and resource controls.
Alternatively implement a different `SpeechProvider` and register its factory
in `speech/service.py` plus validated provider config. Routes, browser and
artifact ownership need not change. Add capability/contract and real benchmark
tests before switching deployment defaults. A remote host needs authenticated
TLS and explicit processing-location/retention review; the MVP's private-network
trust is not sufficient for an internet-addressable endpoint. Streaming can be
added as a separate capability/transport without replacing the buffered API.

A concrete quality-upgrade candidate is [Chatterbox-Turbo](https://huggingface.co/ResembleAI/chatterbox-turbo)
(350M English model, model card declares MIT; reviewed 2 October 2026). It is
not installed, benchmarked or qualified here, and better Daemon speech quality
is not established by its vendor evaluations. For a separately approved pilot:
pin its runtime and weight revision/hashes in a separate GPU image, bake an
operator-owned/licensed reference voice and all assets, and implement the same
`/ready` and `/synthesize` contract. Keep `daemon-default` as the client identity;
change server provider/model configuration only after codec, admission,
cancellation, offline-readiness, peak-memory/latency and blind listening tests
against Kokoro pass. Preserve watermarking and review all runtime/voice licenses.
The larger runtime requires measured sizing, not reuse of Kokoro's resource
limits. No GPU purchase, new dependencies or default switch is approved by this
upgrade description.

Known limits: one English default voice, eSpeak pronunciation, buffered latency,
no cloning, no streaming auto-play, cancellation only between native batches,
temporary global cache rather than durable storage, and no voice-minute billing.
