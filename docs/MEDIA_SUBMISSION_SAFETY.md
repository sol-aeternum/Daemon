# Media submission safety — Phase P0.1 (#469, #488)

This prerequisite implements the **no automatic resubmission** portion of
[ACCOUNT_DELETION_DESIGN.md](ACCOUNT_DELETION_DESIGN.md) §6.2. It does not
enable retired media execution or implement account deletion/reset.

- xAI image and video generation issue one HTTP POST per call. Timeouts,
  transport errors, 429/5xx and malformed successful responses do not cause
  another submission. Existing read-only video polling retries remain.
- fal Kling generation issues one HTTP POST to the existing queue endpoint,
  bypassing the pinned SDK's submission retry wrapper. It also sends the
  documented `X-Fal-No-Retry: 1` header to disable provider queue retries.
  The SDK still handles read-only result retrieval. Submission uses the SDK's
  initialization credential too, preserving existing authentication behavior
  when a wrapper subsequently assigns the client's `api_key` attribute.
- Each submit generates a random UUID before the HTTP call. Successful
  Python result objects and submission exceptions retain it as an internal
  correlation reference; it is not added to result JSON or JSON schemas.
- No reviewed documentation establishes a provider-supported client reference
  or idempotency key for these endpoints. No speculative header or input
  field is sent. The UUID is **not** a server-side lookup/de-duplication key.

## Provider evidence (reviewed 9 October 2026)

- [fal platform headers](https://fal.ai/docs/documentation/model-apis/common-parameters):
  `X-Fal-No-Retry` disables automatic queue retries. `X-Fal-Retry-Config`
  is ignored on shared/public model APIs and is not used here.
- [fal queue API](https://fal.ai/docs/documentation/model-apis/inference/queue):
  the submit response supplies the provider's `request_id`.
- Pinned `fal-client` (version authority: `uv.lock`) `AsyncClient.submit`
  calls `_async_maybe_retry_request`; that wrapper retries transport failures,
  408/409/429 and some ingress failures up to ten times. The provider header
  does not turn off that SDK loop. No SDK global is patched.
- [xAI image reference](https://docs.x.ai/developers/rest-api-reference/inference/images)
  and [video reference](https://docs.x.ai/developers/rest-api-reference/inference/videos):
  no documented client-supplied idempotency/lookup reference was established.
  xAI's `user` field is an abuse-monitoring identifier, not an idempotency key.
  This change preserves existing wire payloads; broader API compatibility is
  not certified by mocked submission-safety tests.

## Acceptance coverage and limits

Design §8 row **“Video submission whose response is lost”**:
`tests/test_xai_imagine.py` and `tests/test_fal_kling.py` mock HTTP and verify
one submit, no retry/backoff, and a pre-submit reference retained on failure.
Success/serialization, cancellation propagation and polling regressions are checked too. This covers
the row's no-resubmission prerequisite, **not the complete deletion outcome**.

Account ownership, pre-submit durable registry/journal intents, operator
uncertainty reports, provider asset deletion and restore/replay are later work
under §5/§6.2/§6.6. The reference here is in-process only: a process crash or
cancellation does not durably retain it. A lost response is not proof that
the provider performed no work. Retired/unqualified media stays unavailable.

There is no Daemon endpoint, payload, SSE, entitlement or database schema
change in this prerequisite. Provider requests deliberately stop retrying;
no deployment, flags, credentials, dependencies or services change.
