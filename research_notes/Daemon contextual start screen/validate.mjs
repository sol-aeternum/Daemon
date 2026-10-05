// Standalone study verification for the contextual start screen.
// Uses the repository's already-installed Playwright. No package install, no
// production server, no API/auth/model call, no browser storage.
//
// Run from the repository root:
//   node "research_notes/Daemon contextual start screen/validate.mjs" /tmp/opencode/<new-dir>
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { chromium } from "../../frontend/node_modules/playwright/index.mjs";

const root = path.dirname(fileURLToPath(import.meta.url));
const output = path.resolve(
  process.argv[2] || "/tmp/opencode/start-screen-validation",
);
await mkdir(output, { recursive: true });
assert.equal(
  (await readdir(output)).length,
  0,
  "Use a new empty output directory; earlier captures are preserved",
);

// Serve only the owned study assets on an ephemeral loopback port.
const files = {
  "/": ["index.html", "text/html; charset=utf-8"],
  "/study.css": ["study.css", "text/css; charset=utf-8"],
  "/study.js": ["study.js", "text/javascript; charset=utf-8"],
};
const server = createServer(async (req, res) => {
  const pathname = new URL(req.url, "http://localhost").pathname;
  if (pathname === "/favicon.ico") {
    res.writeHead(204);
    res.end();
    return;
  }
  if (!files[pathname]) {
    res.writeHead(404);
    res.end();
    return;
  }
  try {
    const [file, type] = files[pathname];
    res.writeHead(200, { "Content-Type": type, "Cache-Control": "no-store" });
    res.end(await readFile(path.join(root, file)));
  } catch {
    res.writeHead(404);
    res.end();
  }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const base = `http://127.0.0.1:${server.address().port}/`;

const report = {
  checkedAt: new Date().toISOString(),
  proposalOnly: true,
  fixtureOnly: true,
  browser: null,
  hashes: {},
  layouts: [],
  interactions: [],
  affordances: { clicked: [], skippedHidden: [], nonClicknable: [] },
  contrast: [],
  screenshots: [],
  errors: [],
  warnings: [],
  externalRequests: [],
};

for (const file of ["index.html", "study.css", "study.js", "validate.mjs"]) {
  report.hashes[file] = createHash("sha256")
    .update(await readFile(path.join(root, file)))
    .digest("hex");
}

let browser;
try {
  browser = await chromium.launch({ headless: true });
  report.browser = `Chromium ${browser.version()}`;
  const context = await browser.newContext({ reducedMotion: "reduce" });
  const page = await context.newPage();
  page.on("pageerror", (error) => report.errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") report.errors.push(`console: ${message.text()}`);
    if (message.type() === "warning") report.warnings.push(message.text());
  });
  await context.route("**/*", (route) => {
    if (!route.request().url().startsWith(base)) {
      report.externalRequests.push(route.request().url());
      return route.abort();
    }
    return route.continue();
  });

  const go = async (query) => {
    await page.goto(`${base}?${query}`);
    await page.locator("#study-bar").waitFor();
  };

  const checkLayout = async (label) => {
    const layout = await page.evaluate(() => {
      const visible = (el) =>
        el.getClientRects().length && getComputedStyle(el).visibility !== "hidden";
      const overflow = [...document.querySelectorAll("body *")]
        .filter(
          (el) =>
            visible(el) &&
            el.getBoundingClientRect().right > innerWidth + 1 &&
            getComputedStyle(el).position !== "absolute",
        )
        .map((el) => el.id || el.className || el.tagName);
      const composer = document.querySelector("#composer");
      const rect = composer.getBoundingClientRect();
      const heading = document.querySelector("#hero-heading");
      return {
        width: innerWidth,
        height: innerHeight,
        pageWidth: document.documentElement.scrollWidth,
        pageHeight: document.documentElement.scrollHeight,
        overflow,
        composerTop: Math.round(rect.top),
        composerBottom: Math.round(rect.bottom),
        heroHeadingHeight: heading ? Math.round(heading.getBoundingClientRect().height) : null,
      };
    });
    assert.ok(
      layout.pageWidth <= layout.width + 1,
      `${label}: page horizontal overflow ${layout.pageWidth}`,
    );
    assert.equal(layout.overflow.length, 0, `${label}: element overflow ${layout.overflow}`);
    assert.ok(
      layout.pageHeight <= layout.height + 1,
      `${label}: page vertical overflow ${layout.pageHeight}`,
    );
    assert.ok(
      layout.composerBottom <= layout.height + 1,
      `${label}: composer below viewport (${layout.composerBottom})`,
    );
    assert.ok(layout.composerTop >= -1, `${label}: composer above viewport`);
    report.layouts.push({ label, ...layout });
    return layout;
  };

  const shot = async (name) => {
    assert.ok(!report.screenshots.includes(`${name}.png`), `Duplicate shot ${name}`);
    await page.screenshot({ path: path.join(output, `${name}.png`) });
    report.screenshots.push(`${name}.png`);
  };

  const expectFocus = async (selector) =>
    assert.ok(
      await page
        .locator(selector)
        .evaluateAll((elements) => elements.some((el) => el === document.activeElement)),
      `Focus expected: ${selector}`,
    );

  const checkCentredPreview = async (action) => {
    const anchor = await action.evaluate((el) => {
      const rect = el.closest(".action-row").getBoundingClientRect();
      return { left: rect.left, width: rect.width };
    });
    const tooltip = await page.locator('.prompt-tooltip:not([hidden])').boundingBox();
    const expectedLeft = Math.max(12, Math.min(anchor.left + (anchor.width - tooltip.width) / 2, page.viewportSize().width - tooltip.width - 12));
    assert.ok(Math.abs(tooltip.x - expectedLeft) <= 1, "Prompt tooltip must be horizontally centred over its row, clamped to the viewport");
  };

  /* ---------------- layout across width x theme x study state -------------- */
  const stories = [
    ["target", "ready"],
    ["mvp", "ready"],
    ["new", "ready"],
    ["error", "failed"],
  ];
  for (const width of [1440, 768, 390, 360]) {
    const height = width === 1440 ? 1000 : width === 768 ? 1024 : 844;
    await page.setViewportSize({ width, height });
    for (const theme of ["dark", "light"]) {
      for (const [story, load] of stories) {
        await go(`state=${story}&load=${load}&theme=${theme}`);
        await checkLayout(`${width}/${theme}/${story}`);
        if (
          (width === 1440 && theme === "dark") ||
          (width === 1440 && theme === "light" && story === "target") ||
          (width === 390 && theme === "dark" && story === "target") ||
          (width === 360 && theme === "light" && story === "new")
        ) {
          await shot(`${width}-${theme}-${story}`);
        }
      }
      // Loading and post-retry states are distinct from empty and error.
      await go(`state=target&load=loading&theme=${theme}`);
      await page.locator(".skeleton-row").first().waitFor();
      await checkLayout(`${width}/${theme}/loading`);
      if (width === 1440 && theme === "dark") await shot(`${width}-${theme}-loading`);

      // Frozen (typing) state and the mobile recents affordance.
      await go(`state=target&load=ready&theme=${theme}`);
      await page.locator("#draft").fill("Unsent question about the kitchen quotes");
      assert.ok(
        await page.locator("#action-list").isHidden(),
        "Suggestions must freeze while a draft exists",
      );
      assert.equal(await page.locator("#draft").inputValue(), "Unsent question about the kitchen quotes");
      await checkLayout(`${width}/${theme}/frozen`);
      if (width === 360 && theme === "dark") await shot(`${width}-${theme}-frozen`);
      await page.locator("#draft").fill("");
      if (width < 1024) {
        await page.locator("#recents-details > summary").click();
        assert.ok(await page.locator("#recents-list").isVisible());
        // Expanding a disclosure can legitimately scroll its containing view.
        // Check the starting layout separately from that user-driven scroll.
        await page.locator("#view-home").evaluate((el) => { el.scrollTop = 0; });
        await checkLayout(`${width}/${theme}/recents`);
        report.interactions.push(`${width}/${theme}: inline recents disclosure opens`);
      }

      // Open conversation preserves draft and attachment; back restores the
      // empty state. The draft is typed after opening, because suggestions
      // deliberately freeze while a draft is being composed.
      await go(`state=mvp&load=ready&theme=${theme}`);
      await page.locator('[data-affordance="row-open"]').first().click();
      await checkLayout(`${width}/${theme}/conversation`);
      await page.locator("#attach-button").click();
      await page.locator("#attach-menu button").first().click();
      await page.locator("#draft").fill("Keep this draft");
      await page.locator("#back-to-start").click();
      assert.equal(await page.locator("#draft").inputValue(), "Keep this draft");
      assert.match(await page.locator("#attachments").textContent(), /flight-options\.pdf/);
      assert.ok(
        await page.locator("#actions-region").isVisible(),
        "Back to start returns to the empty state",
      );
      assert.ok(
        await page.locator("#action-list").isHidden(),
        "Suggestions stay paused while an unsent draft exists",
      );
      assert.match(
        await page.locator(".actions-paused").textContent(),
        /Your text is untouched/,
      );
      report.interactions.push(
        `${width}/${theme}: open conversation and back preserve draft, attachment and empty state`,
      );

      await go(`state=target&load=ready&theme=${theme}`);
      const action = page.locator('[data-affordance="row-start"]').first();
      await action.hover();
      const prompt = await page.locator('.prompt-tooltip:not([hidden])').textContent();
      assert.ok(prompt.length > 200, "Each suggestion has its own detailed prompt");
      assert.equal(await page.locator('[data-affordance="prompt-preview-button"]').count(), 0, "No separate Preview button");
      assert.equal(await page.locator('.action-row .badge').count(), 0, "No Start chat badge on contextual suggestions");
      await action.focus();
      await action.press("End");
      const previewScroll = await page.locator('.prompt-tooltip:not([hidden])').evaluate((el) => ({ top: el.scrollTop, height: el.clientHeight, full: el.scrollHeight }));
      if (previewScroll.full > previewScroll.height) assert.ok(previewScroll.top > 0, "Keyboard can scroll a long prompt preview");
      await action.press("Home");
      await checkCentredPreview(action);
      await checkLayout(`${width}/${theme}/prompt-preview`);
      if (width === 1440 && theme === "dark") await shot("1440-dark-prompt-preview");
      await action.click();
      assert.ok(await page.locator("#view-chat").isVisible());
      assert.equal(await page.locator(".turn-user .turn-bubble").textContent(), prompt);
      assert.match(await page.locator(".turn-user .context-attachment").textContent(), /Context: Lisbon/);
      assert.equal(await page.locator("#draft").inputValue(), "");
      await checkLayout(`${width}/${theme}/suggestion-started-chat`);
      if ((width === 1440 || width === 390) && theme === "dark") {
        await shot(`${width}-${theme}-suggestion-started-chat`);
      }
      await page.locator('.turn-user [data-affordance="context-preview"]').click();
      assert.match(await page.locator(".context-excerpt").textContent(), /Alfama option is 60/);
      await checkLayout(`${width}/${theme}/submitted-source-preview`);
      if (width === 1440 && theme === "dark") await shot("1440-dark-submitted-source-preview");
    }
  }

  /* ----------------------------- interactions ----------------------------- */
  await page.setViewportSize({ width: 1440, height: 1000 });

  // Click immediately submits exactly the previewed prompt and its own source,
  // never unrelated unsent composer text or files. Back restores that draft.
  await go("state=target&load=ready&theme=dark");
  await page.locator("#draft").fill("  Original draft  \n");
  await page.locator("#attach-button").click();
  await page.locator("#attach-menu button").first().click();
  await page.locator('[data-affordance="resume-suggestions"]').click();
  // Replacing the pause control reveals rows under the stationary pointer.
  // Move it away so this test exercises keyboard focus, not a competing hover.
  await page.mouse.move(20, 20);
  await page.locator('[data-affordance="row-start"]').first().focus();
  const exactPrompt = await page.locator('.prompt-tooltip:not([hidden])').textContent();
  assert.equal(await page.locator("#transcript .turn").count(), 0, "Focus preview does not submit");
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
  assert.equal(await page.locator('.prompt-tooltip:not([hidden])').count(), 0);
  await page.locator('[data-affordance="row-start"]').first().press("Enter");
  assert.ok(await page.locator("#view-chat").isVisible());
  assert.equal(await page.locator(".turn-user").count(), 1);
  assert.equal(await page.locator(".turn-user .turn-bubble").textContent(), exactPrompt);
  assert.match(await page.locator(".turn-user").textContent(), /Alfama option is 60/);
  assert.doesNotMatch(await page.locator(".turn-user").textContent(), /Original draft|flight-options/);
  assert.equal(await page.locator("#attachments .attachment").count(), 0);
  assert.equal(await page.locator("#draft").inputValue(), "");
  await page.locator("#draft").fill("Separate unsent reply");
  await page.locator("#back-to-start").click();
  assert.equal(await page.locator("#draft").inputValue(), "  Original draft  \n");
  assert.match(await page.locator("#attachments").textContent(), /flight-options/);
  await page.locator('[data-affordance="demo-open"]').click();
  assert.equal(await page.locator("#draft").inputValue(), "Separate unsent reply");
  assert.equal(await page.locator("#attachments .attachment").count(), 0);
  await page.locator("#new-chat").click();
  assert.equal(await page.locator("#draft").inputValue(), "  Original draft  \n");
  assert.match(await page.locator("#attachments").textContent(), /flight-options/);
  report.interactions.push("Keyboard preview/Escape does not submit; Enter immediately submits exact prompt/context, isolates unsent text/files and restores separate home/reply drafts on navigation");

  // Every target row owns a distinct detailed prompt and exactly one source.
  const seenPrompts = new Set();
  for (let index = 0; index < 3; index += 1) {
    await go("state=target&load=ready&theme=dark");
    const row = page.locator('[data-affordance="row-start"]').nth(index);
    await row.hover();
    await checkCentredPreview(row);
    const prompt = await page.locator('.prompt-tooltip:not([hidden])').textContent();
    assert.ok(prompt.length > 200);
    assert.ok(!seenPrompts.has(prompt));
    seenPrompts.add(prompt);
    await row.click();
    assert.equal(await page.locator(".turn-user .turn-bubble").textContent(), prompt);
    assert.equal(await page.locator(".turn-user .context-attachment").count(), 1);
    assert.equal(await page.locator(".turn-user").count(), 1);
  }
  report.interactions.push("All three task summaries immediately submit their own distinct detailed previewed prompt with one fictional source");

  await go("state=target&load=ready&theme=dark");
  await page.locator('[data-affordance="row-start"]').first().evaluate((button) => {
    button.click();
    button.click();
  });
  assert.equal(await page.locator(".turn-user").count(), 1, "Repeated activation must not submit twice");
  report.interactions.push("Repeated activation of a detached suggestion submits only once");

  const touchContext = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  await touchContext.route("**/*", (route) => {
    if (!route.request().url().startsWith(base)) {
      report.externalRequests.push(route.request().url());
      return route.abort();
    }
    return route.continue();
  });
  const touchPage = await touchContext.newPage();
  touchPage.on("pageerror", (error) => report.errors.push(`touch pageerror: ${error.message}`));
  touchPage.on("console", (message) => {
    if (message.type() === "error") report.errors.push(`touch console: ${message.text()}`);
    if (message.type() === "warning") report.warnings.push(message.text());
  });
  await touchPage.goto(`${base}?state=target&load=ready&theme=dark`);
  assert.equal(await touchPage.locator('[data-affordance="prompt-preview-button"]').count(), 0);
  assert.equal(await touchPage.locator('.action-row .badge').count(), 0);
  const touchPrompt = await touchPage.locator('.prompt-tooltip').first().textContent();
  assert.equal(await touchPage.locator(".turn-user").count(), 0, "Rendering a touch suggestion does not submit");
  await touchPage.screenshot({ path: path.join(output, "390-dark-touch-summary.png") });
  report.screenshots.push("390-dark-touch-summary.png");
  await touchPage.locator('[data-affordance="row-start"]').first().tap();
  assert.equal(await touchPage.locator(".turn-user .turn-bubble").textContent(), touchPrompt);
  assert.equal(await touchPage.locator(".turn-user .context-attachment").count(), 1);
  report.affordances.clicked.push("touch :: row-start");
  report.interactions.push("Touch task summaries have no Start chat badge or Preview button; one tap immediately submits the detailed prompt/context");
  await touchContext.close();

  await go("state=mvp&load=ready&theme=dark");
  await page.locator('[data-affordance="row-open"]').first().click();
  await page.locator("#draft").fill("Unsent after opening");
  await page.locator("#new-chat").click();
  assert.ok(await page.locator("#view-home").isVisible());
  assert.ok(await page.locator("#view-chat").isHidden());
  assert.equal(await page.locator("#composer").count(), 1);
  assert.equal(await page.locator("#draft").inputValue(), "Unsent after opening");
  report.interactions.push("New chat restores empty view with exactly one composer and preserved unsent text");

  for (const query of ["state=new&load=ready", "state=error&load=failed", "state=target&load=loading"]) {
    await go(query);
    await page.locator("#draft").fill("An unsent question");
    assert.equal(await page.locator(".actions-paused").count(), 0, `${query}: no rows means no pause notice`);
    assert.equal(await page.locator('[data-affordance="resume-suggestions"]').count(), 0);
  }
  await go("state=target&load=ready");
  await page.locator('[data-affordance="hide-personal"]').click();
  await page.locator("#draft").fill("Still unsent");
  assert.equal(await page.locator(".actions-paused").count(), 0);
  report.interactions.push("No misleading pause/resume control in empty, error, loading or hidden states");

  // Approved simplification: source labels remain, per-item Why UI is absent.
  await go("state=target&load=ready&theme=dark");
  assert.equal(await page.locator('[data-affordance="row-why"], #source-dialog').count(), 0);
  assert.equal(await page.getByRole("button", { name: /why/i }).count(), 0);
  assert.match(await page.locator(".action-source").first().textContent(), /Lisbon.*last activity/i);
  report.interactions.push("Source labels remain without per-item Why controls or explanation dialog");
  await go("state=mvp&load=ready&theme=dark");
  assert.equal(await page.locator('[data-affordance="row-why"], #source-dialog').count(), 0);

  // Dismiss then undo, without deleting the source conversation.
  await go("state=target&load=ready&theme=dark");
  assert.equal(await page.locator(".action-row").count(), 3);
  await page.locator('[data-affordance="row-dismiss"]').first().click();
  assert.equal(await page.locator(".action-row").count(), 2);
  await page.locator("#toast-undo").click();
  assert.equal(await page.locator(".action-row").count(), 3);
  assert.match(await page.locator("#rail-list").textContent(), /Release notes/);
  report.interactions.push("Dismiss removes one row; undo restores it and the source survives");

  // Hide personal suggestions, then restore.
  await go("state=target&load=ready&theme=dark");
  await page.locator('[data-affordance="hide-personal"]').click();
  assert.match(await page.locator("#actions-region").textContent(), /hidden/i);
  assert.equal(await page.locator(".action-row").count(), 0);
  await page.locator('[data-affordance="show-personal"]').click();
  assert.equal(await page.locator(".action-row").count(), 3);
  report.interactions.push("Hide personal suggestions clears rows and restores them on request");

  // New user vs history error are distinct states.
  await go("state=new&load=ready&theme=light");
  assert.equal(await page.locator(".action-row").count(), 0);
  assert.equal(await page.locator(".status-error").count(), 0);
  assert.match(
    await page.locator("#actions-region").textContent(),
    /nothing here is personalised/i,
  );
  await page.locator('[data-affordance="examples-toggle"]').click();
  await page.locator('[data-affordance="new-user-example"]').first().click();
  assert.match(await page.locator("#draft").inputValue(), /^Explain /);
  assert.equal(await page.locator("#transcript .turn").count(), 0);

  await go("state=error&load=failed&theme=light");
  assert.equal(await page.locator(".action-row").count(), 0);
  assert.match(
    await page.locator(".status-error").textContent(),
    /not the same as having no history/i,
  );
  assert.match(await page.locator(".status-error").textContent(), /no suggestions are shown/i);
  await shot("1440-light-history-error");
  await page.locator('[data-affordance="retry-history"]').click();
  await page.locator(".action-row").first().waitFor();
  assert.equal(await page.locator(".action-row").count(), 3);
  report.interactions.push(
    "New user shows quiet examples; history error is distinct and recovers on retry",
  );

  // Keyboard: Enter sends locally, Shift+Enter and IME Enter do not.
  await go("state=target&load=ready&theme=dark");
  await page.locator("#draft").fill("Line one");
  await page.locator("#draft").press("Shift+Enter");
  await page.locator("#draft").type("Line two");
  assert.equal(await page.locator("#draft").inputValue(), "Line one\nLine two");
  await page.locator("#draft").evaluate((el) => {
    el.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Enter", keyCode: 229, bubbles: true }),
    );
  });
  assert.equal(await page.locator("#transcript .turn").count(), 0, "IME Enter must not submit");
  await page.locator("#draft").press("Enter");
  await page.locator(".turn-user").waitFor();
  assert.equal(await page.locator(".turn-user").count(), 1);
  assert.equal(await page.locator("#draft").inputValue(), "");
  assert.match(await page.locator("#transcript").textContent(), /local demonstration/i);
  assert.equal(
    await page.locator("#attachments").locator(".attachment").count(),
    0,
    "Attachments are cleared on send",
  );
  await shot("1440-dark-local-send");
  await page.locator("#back-to-start").click();
  assert.match(await page.locator("#hero-heading").textContent(), /pick up|do\?/);
  report.interactions.push(
    "Enter sends a local demonstration only; Shift+Enter and IME Enter do not submit; attachments clear on send",
  );

  // Deliberate and Auto are informational mock dialogs.
  await go("state=target&load=ready&theme=dark");
  await page.locator("#deliberate-button").click();
  await page.locator("#deliberate-dialog").waitFor();
  assert.match(await page.locator("#deliberate-dialog").textContent(), /council/i);
  assert.match(await page.locator("#deliberate-dialog").textContent(), /Nothing was submitted/i);
  await page.keyboard.press("Escape");
  await expectFocus("#deliberate-button");
  await page.locator("#auto-button").click();
  await page.locator("#auto-dialog").waitFor();
  assert.match(await page.locator("#auto-dialog").textContent(), /requested nothing/i);
  await page.keyboard.press("Escape");
  await expectFocus("#auto-button");
  assert.equal(await page.locator("#transcript .turn").count(), 0);
  report.interactions.push("Deliberate and Auto open mock dialogs; neither submits a request");

  // Attach menu keyboard behaviour.
  await go("state=target&load=ready&theme=dark");
  await page.locator("#attach-button").click();
  await page.locator("#attach-button").press("ArrowDown");
  await page.locator("#attach-menu button").first().press("Escape");
  assert.equal(await page.locator("#attach-menu").isHidden(), true);
  await expectFocus("#attach-button");
  report.interactions.push("Attachment menu closes on Escape and returns focus to its opener");

  /* ------------------------- affordance coverage ------------------------- */
  const clickable = 'button[data-affordance], summary[data-affordance], select[data-affordance]';
  const untagged = await page.$$eval(
    "button, summary, select",
    (elements) =>
      elements.filter((el) => !el.dataset.affordance).map((el) => el.outerHTML.slice(0, 60)),
  );
  report.affordances.nonClicknable = untagged;
  assert.equal(untagged.length, 0, `Clickable controls without data-affordance: ${untagged}`);

  // Exercise named affordances with explicit setup. This is bounded interaction
  // coverage, not proof of a complete accessibility or every-control audit.
  const viewportFor = { mobile: { width: 390, height: 844 } };
  const sweep = [
    ["1440", "state=target&load=ready&theme=dark", [
      "row-start",
      "row-dismiss",
      "hide-personal",
      "attach-button",
      "auto-button",
      "deliberate-button",
      "new-chat",
      "rail-search",
      "explain-toggle",
    ]],
    ["1440", "state=target&load=ready&theme=dark", ["study-reset"], ["study-toggle"]],
    ["1440", "state=target&load=ready&theme=dark", ["attach-sample"], ["attach-button"]],
    ["1440", "state=mvp&load=ready&theme=dark", ["row-open"]],
    ["1440", "state=target&load=ready&theme=dark", ["back-to-start"], ["row-start"]],
    ["1440", "state=target&load=ready&theme=dark", ["demo-open"], ["row-start", "back-to-start"]],
    ["1440", "state=target&load=ready&theme=dark", ["toast-undo", "toast-close"], ["row-dismiss"]],
    ["1440", "state=target&load=ready&theme=dark", ["send-button"], ["fill:demo send"]],
    ["1440", "state=target&load=ready&theme=light", ["attachment-remove"], ["attach-button", "attach-sample"]],
    ["1440", "state=target&load=ready&theme=dark", ["context-preview"], ["row-start"]],
    ["1440", "state=new&load=ready&theme=dark", ["new-user-example"], ["examples-toggle"]],
    ["1440", "state=target&load=ready&theme=dark", ["show-personal"], ["hide-personal"]],
    ["1440", "state=target&load=ready&theme=dark", ["resume-suggestions"], ["fill:keep me"]],
    ["1440", "state=error&load=failed&theme=dark", ["retry-history"]],
    ["mobile", "state=target&load=ready&theme=dark", ["recents-toggle"]],
    ["mobile", "state=target&load=ready&theme=dark", ["recents-open"], ["recents-toggle"]],
    ["mobile", "state=target&load=ready&theme=dark", ["study-state", "study-load", "study-theme"], ["study-toggle"]],
  ];
  for (const [viewport, query, affordances, setup] of sweep) {
    await page.setViewportSize(viewport === "mobile" ? viewportFor.mobile : { width: 1440, height: 1000 });
    for (const affordance of affordances) {
      await go(query);
      if (setup) {
        for (const step of setup) {
          if (step.startsWith("fill:")) await page.locator("#draft").fill(step.slice(5));
          else await page.locator(`[data-affordance="${step}"]`).first().click();
        }
      }
      const target = page.locator(`[data-affordance="${affordance}"]`).first();
      if (!(await target.count()) || !(await target.isVisible())) {
        report.affordances.skippedHidden.push(`${viewport} ${query} :: ${affordance}`);
        continue;
      }
      const before = report.errors.length;
      await target.click();
      await page.waitForTimeout(80);
      assert.equal(
        report.errors.length,
        before,
        `${affordance} at ${query} raised a browser error`,
      );
      report.affordances.clicked.push(`${viewport} ${query} :: ${affordance}`);
      assert.ok(await page.locator("#composer").isVisible(), "Composer survived the click");
      assert.ok(await page.locator("#study-bar").isVisible(), "Study controls survived the click");
    }
  }
  await page.setViewportSize({ width: 1440, height: 1000 });

  // Verify the small removable attachment target directly in both themes.
  for (const theme of ["dark", "light"]) {
    await page.setViewportSize({ width: 390, height: 844 });
    await go(`state=target&load=ready&theme=${theme}`);
    await page.locator("#attach-button").click();
    await page.locator("#attach-menu button").first().click();
    const rect = await page.locator(".attachment-remove").boundingBox();
    assert.ok(rect.width >= 44 && rect.height >= 44, `${theme}: attachment remove target must be >=44px`);
    await page.locator(".attachment-remove").click();
    assert.equal(await page.locator(".attachment").count(), 0);
  }
  report.interactions.push("Attachment removal has a 44px target in both mobile themes");
  await page.setViewportSize({ width: 1440, height: 1000 });

  // Sample computed text contrast. Not a complete WCAG audit.
  for (const theme of ["dark", "light"]) {
    await go(`state=target&load=ready&theme=${theme}`);
    await page.locator('[data-affordance="row-start"]').first().hover();
    const samples = await page.evaluate(() => {
      const luminance = (color) =>
        color
          .match(/[\d.]+/g)
          .slice(0, 3)
          .map(Number)
          .map((v) => {
            v /= 255;
            return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
          })
          .reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
      const background = (el) => {
        const color = getComputedStyle(el).backgroundColor;
        return color === "rgba(0, 0, 0, 0)" ? background(el.parentElement) : color;
      };
      return [
        ".hero-heading",
        ".action-label",
        ".action-source",
        ".mock-note",
        ".composer-hint",
        ".deliberate-btn",
        ".actions-hide",
        ".prompt-tooltip:not([hidden])",
        ".study-lede",
        ".tool-btn",
      ].map((selector) => {
        const el = document.querySelector(selector);
        if (!el) return { selector, missing: true };
        const fg = getComputedStyle(el).color;
        const bg = background(el);
        const a = luminance(fg);
        const b = luminance(bg);
        return {
          selector,
          fg,
          bg,
          ratio: Math.round(((Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)) * 100) / 100,
        };
      });
    });
    for (const sample of samples) {
      assert.ok(!sample.missing, `Expected contrast selector missing: ${sample.selector}`);
      report.contrast.push({ theme, ...sample });
      assert.ok(
        sample.ratio >= 4.5,
        `${theme}/${sample.selector} text contrast ${sample.ratio} < 4.5`,
      );
    }
  }

  // No storage, no cookies, no storage-backed persistence in the study.
  const persistence = await page.evaluate(() => ({
    local: localStorage.length,
    session: sessionStorage.length,
  }));
  assert.equal(persistence.local, 0, "Study must not write localStorage");
  assert.equal(persistence.session, 0, "Study must not write sessionStorage");

  await page.reload();
  await page.locator("#study-bar").waitFor();
  assert.equal(await page.locator("#draft").inputValue(), "", "Draft is not persisted");
  assert.equal(await page.locator("#context-attachments .context-attachment").count(), 0, "Source context is not persisted");
  assert.equal(
    await page.locator(".action-row").count(),
    3,
    "Dismissals are not persisted across reload",
  );

  assert.equal(report.errors.length, 0, `Browser errors: ${report.errors}`);
  assert.equal(report.externalRequests.length, 0, `External requests: ${report.externalRequests}`);
  report.status = "passed";
} catch (error) {
  report.status = "failed";
  report.failure = error.stack;
  process.exitCode = 1;
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
  await writeFile(
    path.join(output, "validation.json"),
    `${JSON.stringify(report, null, 2)}\n`,
  );
  console.log(
    JSON.stringify(
      {
        status: report.status,
        layouts: report.layouts.length,
        interactions: report.interactions.length,
        affordancesClicked: report.affordances.clicked.length,
        affordancesSkipped: report.affordances.skippedHidden.length,
        screenshots: report.screenshots.length,
        errors: report.errors,
        warnings: report.warnings,
        externalRequests: report.externalRequests,
        failure: report.failure,
        output,
      },
      null,
      2,
    ),
  );
}
