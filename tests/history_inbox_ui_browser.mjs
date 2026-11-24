// Drives the actual app. Only the lost-ACK arm intercepts a response, and
// drops it after the real upstream confirms acceptance. All reads are live.
import assert from "node:assert/strict";
import { createInterface } from "node:readline";
import { chromium } from "../frontend/node_modules/playwright/index.mjs";

const lines = createInterface({ input: process.stdin });
const frames = lines[Symbol.asyncIterator]();
const { ui, backend, bootstrap, lost } = JSON.parse(
  (await frames.next()).value,
);
const browser = await chromium.launch();
const pilot = backend + "/tasks/inbox-pilot";
const prompt = "Do a deep dive on X.";
const followup = "How’s that going?";
const running = "Last saved as running.";
const accepted = "Accepted.";
const unavailable = "Status unavailable";
const report = "Synthetic research report: X. Fixture findings only.";
let phase = "initial";
let closing;
let sendCount = 0;
let lostCount = 0;
const external = [];
const receipt = (stage, values = {}) =>
  console.log(JSON.stringify({ stage, ...values }));

const closeBrowser = () => (closing ??= browser.close());
process.once("SIGTERM", async () => {
  await closeBrowser();
  lines.close();
  process.exit(143);
});

async function open(width, height) {
  const context = await browser.newContext({
    viewport: { width, height },
    serviceWorkers: "block",
  });
  await context.route("**/*", async (route) => {
    const req = route.request();
    const origin = new URL(req.url()).origin;
    if (origin !== ui && origin !== backend) {
      external.push(origin);
      return route.abort("blockedbyclient");
    }
    if (req.url() === pilot && req.method() === "POST") {
      sendCount += 1;
      if (lost && sendCount === 1) {
        try {
          const upstream = await route.fetch({ maxRedirects: 0, maxRetries: 0 });
          if (upstream.status() === 200) lostCount += 1;
          await upstream.dispose();
        } catch {
          // The main test checks lostCount. Do not log a possibly
          // credential-bearing transport exception.
        }
        return route.abort("connectionclosed");
      }
    }
    return route.continue();
  });
  const page = await context.newPage();
  page.setDefaultTimeout(30_000);
  return { context, page };
}

const composer = (page) => page.getByRole("textbox", { name: "Message Daemon" });
const send = (page) => page.getByRole("button", { name: "Send message" });
const text = (page, value) => page.getByText(value, { exact: true });
const link = (page) => page.getByRole("link", { name: "Open report" });

async function ask(page, responseStatus) {
  const seen = page.waitForResponse(
    (r) => r.url() === pilot && r.request().method() === "GET",
  );
  await composer(page).fill(followup);
  await send(page).click();
  const response = await seen;
  if (responseStatus !== undefined)
    assert.equal(response.status(), responseStatus);
  assert.match(response.headers()["cache-control"] ?? "", /no-store/);
  return response;
}

try {
  phase = "desktop-setup";
  const desktop = await open(1440, 960);
  const first = desktop.page;
  await first.goto(ui + "/setup");
  await first.getByPlaceholder("Paste your setup token here").fill(bootstrap);
  const issued = first.waitForResponse(
    (r) => new URL(r.url()).pathname === "/api/v1/auth/setup",
  );
  await first.getByRole("button", { name: "Complete Setup" }).click();
  const setup = await issued;
  assert.equal(setup.status(), 200);
  const enrollment = await first.evaluate(async (token) => {
    const response = await fetch("/api/v1/auth/enroll/start", {
      method: "POST",
      headers: { Authorization: "Bearer " + token },
    });
    if (!response.ok) throw Error("Enrollment start refused");
    return response.json();
  }, (await setup.json()).access_token);

  phase = "desktop-send";
  await first.goto(ui + "/inbox");
  const sent = first.waitForRequest(
    (r) => r.url() === pilot && r.method() === "POST",
  );
  await composer(first).fill(prompt);
  await send(first).click();
  const request = await sent;
  const key = await request.headerValue("idempotency-key");
  assert.match(key ?? "", /^[a-zA-Z0-9-]{1,128}$/);
  assert.equal(request.postDataJSON().text, prompt);
  await text(first, lost ? unavailable : accepted).waitFor();
  assert.equal(await link(first).count(), 0);
  if (lost) assert.equal(await text(first, accepted).count(), 0);
  receipt("desktop", { key, lost: lostCount });
  assert.equal((await frames.next()).value, "running verified");
  await desktop.context.close();

  phase = "phone-enrollment";
  const phone = await open(390, 844);
  const second = phone.page;
  await second.goto(ui + "/setup");
  await second.getByLabel("Enrollment Link or Token").fill(enrollment.qr_payload);
  const enrolled = second.waitForResponse(
    (r) => new URL(r.url()).pathname === "/api/v1/auth/enroll/complete",
  );
  await second.getByRole("button", { name: "Complete Enrollment" }).click();
  const completedEnrollment = await enrolled;
  assert.equal(completedEnrollment.status(), 200);
  const phoneToken = (await completedEnrollment.json()).access_token;
  await composer(second).waitFor(); // Normal setup navigation to chat.
  await second.goto(ui + "/inbox");

  phase = "phone-followup-and-refresh";
  await text(second, running).waitFor();
  await ask(second, 200);
  await text(second, running).waitFor();
  assert.equal(await link(second).count(), 0);
  await second.reload();
  await text(second, running).waitFor();
  const geometry = await send(second).evaluate((button) => {
    const rect = button.getBoundingClientRect();
    return {
      width: innerWidth,
      fits:
        document.documentElement.scrollWidth <= innerWidth &&
        rect.left >= 0 &&
        rect.right <= innerWidth &&
        rect.top >= 0 &&
        rect.bottom <= innerHeight,
      tapHeight: rect.height,
    };
  });
  assert.equal(geometry.width, 390);
  assert.ok(geometry.fits && geometry.tapHeight >= 44);
  assert.equal(sendCount, 1);
  receipt("phone", geometry);
  const foreignAuthorization = JSON.parse((await frames.next()).value).Authorization;
  assert.equal((await frames.next()).value, "report committed");

  phase = "phone-report";
  await ask(second, 200);
  const reportLink = link(second);
  await reportLink.waitFor();
  assert.equal(await reportLink.count(), 1);
  const href = await reportLink.getAttribute("href");
  const reportUrl = new URL(href, ui);
  assert.equal(reportUrl.origin, ui);
  assert.equal(reportUrl.pathname, "/");
  const conversation = reportUrl.searchParams.get("id");
  assert.ok(conversation);
  const response = second.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === "/conversations/" + conversation &&
      r.request().method() === "GET",
  );
  await reportLink.click();
  const detail = await response;
  assert.equal(detail.status(), 200);
  const saved = await detail.json();
  assert.equal(saved.latest_task.status, "completed");
  assert.equal(saved.messages.at(-1).content, report);
  await second
    .getByLabel("Conversation messages")
    .getByText(report, { exact: true })
    .waitFor();
  const task = saved.latest_task.id;
  const message = saved.messages.at(-1).id;

  phase = "same-key-retry";
  const replay = await second.evaluate(
    async ({ pilot, phoneToken, key, prompt }) =>
      (
        await fetch(pilot, {
          method: "POST",
          cache: "no-store",
          headers: {
            Authorization: "Bearer " + phoneToken,
            "Idempotency-Key": key,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ text: prompt }),
        })
      ).status,
    { pilot, phoneToken, key, prompt },
  );
  assert.equal(replay, 409);
  await second.goto(ui + "/inbox");
  await link(second).waitFor();
  assert.equal(await link(second).getAttribute("href"), href);

  phase = "foreign-account";
  const foreign = await open(390, 844);
  await foreign.page.goto(ui + "/setup");
  const access = await foreign.page.evaluate(
    async ({ pilot, backend, foreignAuthorization, task, conversation }) => {
      const headers = { Authorization: foreignAuthorization };
      const unknown = crypto.randomUUID();
      const results = await Promise.all(
        [
          pilot,
          `${backend}/tasks/${task}`,
          `${backend}/conversations/${conversation}`,
          `${backend}/tasks/${unknown}`,
          `${backend}/conversations/${unknown}`,
        ].map((url) => fetch(url, { headers, cache: "no-store" })),
      );
      return {
        own: (await results[0].json()).state,
        codes: results.map((result) => result.status),
        masked:
          (await results[1].text()) === (await results[3].text()) &&
          (await results[2].text()) === (await results[4].text()),
      };
    },
    { pilot, backend, foreignAuthorization, task, conversation },
  );
  assert.deepEqual(access, {
    own: "empty",
    codes: [200, 404, 404, 404, 404],
    masked: true,
  });
  await foreign.context.close();
  receipt("report", { task, conversation, message, replay, foreign: access.codes });
  assert.equal((await frames.next()).value, "original locked");

  phase = "unavailable-status";
  const refused = await ask(second);
  assert.ok([409, 503].includes(refused.status()));
  await text(second, unavailable).waitFor();
  assert.equal(await link(second).count(), 0);
  assert.equal(sendCount, 2);
  assert.equal(lostCount, lost ? 1 : 0);
  assert.deepEqual(external, []);
  receipt("finished", { unavailable: refused.status(), starts: sendCount });
} catch (error) {
  // Browser exceptions can include credential-bearing request data.
  console.error(JSON.stringify({ failure: error.name, phase }));
  process.exitCode = 1;
} finally {
  await closeBrowser();
  lines.close();
}
