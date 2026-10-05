# Daemon contextual start screen

**Option B product direction approved — fictional study, no production integration.**

Owner amendment, 5 October 2026: remove every per-item “Why” control and its
explanation dialog. Keep compact source labels and internal grounding.

Owner interaction decision, 5 October 2026: **send immediately in a new chat**.
Each contextual suggestion shows a short task summary and has its own detailed
prompt ready. Hover or keyboard focus previews that exact prompt. Clicking or
tapping the task submits its prompt plus
fictional source context immediately, without another Send step. This supersedes
the earlier draft-and-wait behaviour. Prompt preview is not a “Why” explanation.
The owner's subsequent cleanup removes the **Start chat** badge and separate
**Preview** button: only the task summary, compact source label and dismiss remain.
Touch-only users tap to start immediately; there is no separate touch preview.
Owner layout amendment: centre the hover prompt horizontally over its suggestion
row, keeping it inside the viewport on narrow screens.

Open [index.html](index.html) in a browser. Use **Study controls** to switch
between the proposed semantic target, metadata-only MVP, new user and
unavailable-history specimens, and between dark/light themes.

The mock has one composer. Target suggestions create a new **local demonstration**
turn immediately. Metadata-only recents open an existing fictional chat without
submitting. No actual API request occurs. Source labels, prompt previews,
dismiss/undo, hide, mock attachments and Council disclosure are interactive.
Unrelated unsent draft text and files are not included in suggestion submission;
Back to start/New chat restores them. The local demonstration can be reopened
from the desktop rail, with its separate reply draft preserved while navigating
away/back to that demonstration. This disposable study retains only the latest
local demonstration; starting another suggestion replaces that demo and its
reply draft, not the unrelated home draft. Production conversation retention
is not implemented here.
Reload clears all state. There are no model/API calls or persistent browser data.
Local Send clears mock file chips without recording file contents (unchanged
baseline behaviour); retained source excerpts are recorded on the local turn.

Production context binding and relevance generation are **not implemented**.
The target uses a fixed fixture list; submitted context contains the fixture's
invented turns, inspectable on the demonstration user turn.
This is not runtime source fidelity, permission enforcement or a chat API contract.
Fixture context can be incomplete; missing facts must not be invented.
Model preferences, Council compatibility and existing auth contracts are not
retired by this study.

## Report and screenshots

- [Research and proposal](../../reports/Daemon%20contextual%20start%20screen.md)
- [Desktop dark target](screenshots/1440-dark-target.png)
- [Desktop light target](screenshots/1440-light-target.png)
- [Mobile target](screenshots/390-dark-target.png)
- [Desktop detailed prompt hover preview](screenshots/1440-dark-prompt-preview.png)
- [Touch task summaries without extra badges/buttons](screenshots/390-dark-touch-summary.png)
- [Desktop suggestion-started chat](screenshots/1440-dark-suggestion-started-chat.png)
- [Mobile suggestion-started chat](screenshots/390-dark-suggestion-started-chat.png)
- [Submitted fictional source text](screenshots/1440-dark-submitted-source-preview.png)
- [Metadata-only MVP](screenshots/1440-dark-mvp.png)
- [New user](screenshots/1440-dark-new.png)
- [Unavailable history](screenshots/1440-dark-error.png)

## Verified study checks

```sh
node "research_notes/Daemon contextual start screen/validate.mjs" /tmp/opencode/daemon-start-YOUR-NEW-RUN
```

Uses the existing repository Playwright installation. The runner creates a
restricted ephemeral loopback server, blocks external browser requests and
shuts down its browser/server on completion. Use a new empty output directory.

The primary's final study run `/tmp/opencode/daemon-start-centred-tooltip-check-01` passed
86 layout checks, 28 interaction checks and 29 exercised controls (none skipped),
with zero browser errors, warnings or external requests. Widths: 360, 390, 768
and 1440 CSS pixels; dark/light themes; ready/loading/error/new-user/drafting
and conversation states. `validation.json` preserves hashes of the checked
HTML, CSS, JS and runner. Selected screenshots are copied from that run.

Checks include immediate submission of the exact previewed prompt/context,
no submission on hover/focus, Escape dismissal, one submission on repeated
activation, centred prompt placement with viewport clamping in both themes/all
four widths, distinct detailed prompts for all three suggestions,
isolation/restoration of unrelated drafts and files, no Start chat/Preview UI,
direct touch submission, exact draft whitespace,
attachment retention, open/back/new-chat navigation, a single composer,
submitted fictional source inspection,
dismiss/undo/hide, absence of per-item Why UI, IME/Shift+Enter behaviour,
and storage absence. Sampled text contrast is not a complete accessibility audit.
Review fixes are covered by negative checks for misleading pause controls,
and 44px mobile attachment-removal targets. The
empty-state attachment menu opens below the composer so it remains clickable.
Home content can scroll; expanding recents may legitimately move the composer
out of view until the user scrolls back.

No authenticated runtime, real account isolation, provider inference, actual
source binding, mobile software keyboard, screen reader or complete WCAG audit
was tested. Full application gates were not run: runtime files and release
status are unchanged. The desktop Review pane was disconnected; screenshots
were inspected locally instead.

## Independent review

The pre-approval baseline received fresh read-only `review-go` review of
Bunny/Sol-authored artifacts without
executing tests. Three low-priority findings (pause notice in states without
rows, attachment target size, source-panel coverage) were accepted and repaired.
The reported medium-priority persistent-override mismatch was resolved by
correcting the intended contract: **Show them anyway** is temporary; the next
edit/view refresh pauses rows again. No persistent override is promised. The
primary verified the integrated final state with the passing run above.

The owner's subsequent no-Why amendment was directly checked by the primary:
no row explanation button or source dialog remains, compact source labels still
render, and the existing dismiss/undo/hide paths pass. The older explanation
screenshot is retained only in `screenshots/historical/`, not as a current design.

The superseded draft-only new-chat amendment received fresh read-only `review-go` review
(`ses_ef51f7de8ffe3RIcC6VCa547LF`) of the Sol-authored changes. No blocking
defects were found. Two low-priority gaps were accepted: directly measure
context-removal targets and document that Suggested prompts also attach context.
Both were addressed before that amendment's passing run. The unchanged file-chip
clearing on local Send is documented; the suggested new-chat DOM fragility is
not a current defect because that handler changes neither attachment state.
The primary inspected desktop/mobile captures and verified checked asset hashes.
Its prior screenshots are retained under `screenshots/historical/`; they do not
describe the current immediate-start interaction.

The immediate-start amendment received fresh read-only `review-go` review
(`ses_ef506d0ccffe7Lc7AE9mhmcg8S`) and one bounded final cleanup check. No
blocking behaviour findings remained. The initially stale in-repo validation
copy was replaced with the final passing run and its four asset hashes verified
by the primary. The latest-demo-only reply-draft limitation was clarified above.
The reviewer could inspect the final in-repo evidence but not external `/tmp`
paths or execute tests; final executable verification belongs to the primary.
The primary inspected current desktop hover and mobile summary screenshots.
The later tooltip-centering-only amendment was directly verified by the primary
with geometry assertions and refreshed screenshots/hashes; no new independent
review was needed for that small positioning change.

Earlier failed prototype runs remain under their original `/tmp/opencode`
output directories. They are not release blockers in the application, which
was not changed. No full accessibility or production-readiness claim is made.
