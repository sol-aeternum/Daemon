// Actual loopback UI, independent authenticated device sessions, two native originals.
import assert from "node:assert/strict";
import { createInterface } from "node:readline";
import { chromium } from "../frontend/node_modules/playwright/index.mjs";

const lines = createInterface({ input: process.stdin });
const frames = lines[Symbol.asyncIterator]();
const { ui, backend, bootstrap, managedRestart, questions, retrieved } = JSON.parse((await frames.next()).value);
const browser = await chromium.launch();
const pilot = backend + "/tasks/inbox-pilot";
const receipt = (stage, values = {}) => console.log(JSON.stringify({ stage, ...values }));
let phase = "setup";
let starts = 0;
let lost = 0;
const external = [];
const prompt = (topic) => questions
  ? `Research: ${questions[topic === "X" ? 0 : 1]}`
  : `Do a deep dive on ${topic}.`;
const clarification = questions ? /Which question do you mean/ : /Do you mean research X or Y/;
const isPilot = (url) => new URL(url).origin + new URL(url).pathname === pilot;
const composer = (page) => page.getByRole("textbox", { name: "Message Daemon" });
const button = (page) => page.getByRole("button", { name: "Send message" });
const status = (page) => page.getByRole("status");
const link = (page) => page.getByRole("link", { name: "Open report" });
let closing;
const close = () => (closing ??= browser.close());
process.once("SIGTERM", async () => {
  await close();
  lines.close();
  process.exit(143);
});

async function open(width, height) {
  const context = await browser.newContext({ viewport: { width, height }, serviceWorkers: "block" });
  await context.route("**/*", async (route) => {
    const request = route.request();
    const origin = new URL(request.url()).origin;
    if (origin !== ui && origin !== backend) {
      external.push(origin);
      return route.abort("blockedbyclient");
    }
    if (isPilot(request.url()) && request.method() === "POST") {
      starts++;
      if (starts === 2) {
        try {
          const response = await route.fetch({ maxRedirects: 0, maxRetries: 0 });
          if (response.status() === 200) lost++;
          await response.dispose();
        } catch {
          // Do not print credential-bearing error objects.
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

async function send(page, message, method) {
  const request = page.waitForRequest((r) => isPilot(r.url()) && r.method() === method);
  await composer(page).fill(message);
  await button(page).click();
  return request;
}

async function ask(page, message, expectedTopic, expectedState) {
  const response = page.waitForResponse((r) =>
    isPilot(r.url()) && r.request().method() === "GET" &&
    new URL(r.url()).searchParams.get("topic") === expectedTopic);
  if (questions && expectedTopic) {
    const question = questions[expectedTopic === "X" ? 0 : 1];
    const choice = page.getByRole("button", {name: question, exact: true});
    if (await choice.count()) await choice.click();
    else await send(page, question, "GET");
  } else {
    await send(page, message, "GET");
  }
  const result = await response;
  receipt("diagnostic", { phase, status: result.status() });
  assert.equal(result.status(), 200);
  assert.match(result.headers()["cache-control"] ?? "", /no-store/);
  const data = await result.json();
  receipt("diagnostic", { phase, state: data.state, topic: data.topic ?? null });
  assert.equal(data.state, expectedState);
  if (expectedTopic) assert.equal(data.topic, expectedTopic);
  return { data, result };
}

async function readReport(page, original, content) {
  await link(page).waitFor();
  assert.equal(await link(page).count(), 1);
  const url = new URL(await link(page).getAttribute("href"), ui);
  assert.equal(url.origin, ui);
  assert.equal(url.searchParams.get("id"), original.conversation);
  const response = page.waitForResponse((r) =>
    new URL(r.url()).pathname === "/conversations/" + original.conversation &&
    r.request().method() === "GET");
  await link(page).click();
  const detail = await response;
  assert.equal(detail.status(), 200);
  const saved = await detail.json();
  assert.equal(saved.latest_task.id, original.task);
  assert.equal(saved.messages.at(-1).id, original.message);
  assert.equal(saved.messages.at(-1).content, content);
  const messages = page.getByLabel("Conversation messages");
  if (questions) {
    // Markdown rendering splits prose from citation anchors. Compare the saved
    // body above byte-for-byte, then verify the actual source links in the DOM.
    assert.ok(original.sources.length > 0);
    for (const source of original.sources) {
      await messages.locator(`a[href="${source}"]`).first().waitFor();
    }
  } else {
    await messages.getByText(content, { exact: true }).waitFor();
  }
}

async function enrollPhone(enrollment) {
  const phone = await open(390, 844);
  const page = phone.page;
  await page.goto(ui + "/setup");
  await page.getByLabel("Enrollment Link or Token").fill(enrollment.qr_payload);
  const enrolled = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/v1/auth/enroll/complete");
  await page.getByRole("button", { name: "Complete Enrollment" }).click();
  assert.equal((await enrolled).status(), 200);
  await composer(page).waitFor();
  return page;
}

try {
  const desktop = await open(1440, 960);
  const first = desktop.page;
  await first.goto(ui + "/setup");
  await first.getByPlaceholder("Paste your setup token here").fill(bootstrap);
  const issued = first.waitForResponse((r) => new URL(r.url()).pathname === "/api/v1/auth/setup");
  await first.getByRole("button", { name: "Complete Setup" }).click();
  const setup = await issued;
  assert.equal(setup.status(), 200);
  const enrollment = await first.evaluate(async (token) => {
    const response = await fetch("/api/v1/auth/enroll/start", {
      method: "POST", headers: { Authorization: "Bearer " + token },
    });
    if (!response.ok) throw Error("Enrollment refused");
    return response.json();
  }, (await setup.json()).access_token);
  phase = "first-original";
  await first.goto(ui + "/inbox");
  const xRequest = await send(first, prompt("X"), "POST");
  const keyX = await xRequest.headerValue("idempotency-key");
  await status(first).getByText(/Accepted/).waitFor();
  receipt("first", { key: keyX });
  const x = JSON.parse((await frames.next()).value);
  let secondSender = first;
  if (managedRestart) {
    phase = "successor-independent-phone";
    await desktop.context.close();
    secondSender = await enrollPhone(enrollment);
    await secondSender.goto(ui + "/inbox");
  }
  const selectedX = await ask(secondSender, "X", "X", "completed");
  assert.equal(selectedX.data.conversation_id, x.conversation);
  assert.deepEqual(selectedX.data.can_start, ["Y"]);
  await link(secondSender).waitFor();
  if (managedRestart) {
    await readReport(secondSender, x, "Synthetic report X. First fixture.");
    receipt("recovered", { starts, x: x.task });
    assert.equal((await frames.next()).value, "explicit Y");
    await secondSender.goto(ui + "/inbox");
    await composer(secondSender).waitFor();
  } else if (retrieved) {
    phase = "desktop-original-report";
    await readReport(secondSender, x, x.content);
    await secondSender.goto(ui + "/inbox");
    await composer(secondSender).waitFor();
  }

  phase = "second-lost-ack";
  const yRequest = await send(secondSender, prompt("Y"), "POST");
  const keyY = await yRequest.headerValue("idempotency-key");
  assert.notEqual(keyX, keyY);
  await secondSender.getByText("Status unavailable", { exact: true }).waitFor();
  assert.equal(await link(secondSender).count(), 0);
  assert.equal(lost, 1);
  receipt("second", { key: keyY, lost });
  const y = JSON.parse((await frames.next()).value);
  if (!managedRestart) await desktop.context.close();

  phase = "independent-phone";
  const page = managedRestart ? secondSender : await enrollPhone(enrollment);
  phase = "phone-reopen";
  await composer(page).waitFor();
  await page.goto(ui + "/inbox");
  await status(page).getByText(clarification).waitFor();
  phase = "phone-generic-followup";
  const ambiguous = await ask(page, "How’s that going?", null, "ambiguous");
  assert.deepEqual(ambiguous.data.choices, ["X", "Y"]);
  if (questions) assert.deepEqual(ambiguous.data.research_questions, questions);
  assert.equal(await link(page).count(), 0);
  phase = "phone-select-running";
  await ask(page, "Y", "Y", "running");
  await status(page).getByText(/running/i).waitFor();
  assert.equal(await link(page).count(), 0);
  phase = "phone-reload";
  await page.reload();
  await status(page).getByText(clarification).waitFor();
  phase = "phone-expiry";
  // The existing global auth coordination gate may temporarily unmount all
  // protected children during refresh. Reopen must be a generic read, not a
  // guessed selection or a replay. Explicitly select X after that read.
  await page.clock.setFixedTime(new Date(Date.now() + 31 * 60 * 1000));
  const refreshed = page.waitForResponse((r) =>
    new URL(r.url()).pathname === "/api/v1/auth/refresh" && r.request().method() === "POST");
  const afterRefresh = await ask(page, "How’s that going?", null, "ambiguous");
  const refresh = await refreshed;
  assert.equal(refresh.status(), 200);
  const rotated = (await refresh.json()).access_token;
  assert.ok((await afterRefresh.result.request().headerValue("authorization")) === "Bearer " + rotated);
  await status(page).getByText(clarification).waitFor();
  assert.equal(await link(page).count(), 0);
  await page.clock.setFixedTime(new Date());
  phase = "phone-select-after-refresh";
  const ready = await ask(page, "X", "X", "completed");
  assert.ok((await ready.result.request().headerValue("authorization")) === "Bearer " + rotated);
  phase = "phone-first-report";
  await readReport(page, x, questions ? x.content : "Synthetic report X. First fixture.");
  assert.equal(starts, 2);
  receipt("phone", { refreshed: true, starts });
  assert.equal((await frames.next()).value, "second committed");
  phase = "second-report";
  await page.goto(ui + "/inbox");
  await status(page).getByText(clarification).waitFor();
  await ask(page, "Y", "Y", "completed");
  const finalY = questions ? JSON.parse((await frames.next()).value) : y;
  assert.equal(finalY.task, y.task);
  await readReport(page, finalY, questions ? finalY.content : "Synthetic report Y. Second fixture.");
  if (questions) {
    const protectedReads = [pilot + "?topic=Y", backend + "/conversations/" + y.conversation];
    for (const change of ["expired", "revoked"]) {
      receipt("auth-check", { change });
      assert.equal((await frames.next()).value, change);
      const statuses = await page.evaluate(async ({ urls, token }) => {
        const responses = [];
        for (const url of urls) {
          const response = await fetch(url, {
            headers: { Authorization: `Bearer ${token}` },
            cache: "no-store",
            credentials: "omit",
            redirect: "error",
          });
          responses.push(response.status);
        }
        return responses;
      }, { urls: protectedReads, token: rotated });
      assert.deepEqual(statuses, [401, 401]);
    }
  }
  assert.equal(starts, 2);
  assert.deepEqual(external, []);
  receipt("finished", { starts, lost, x: x.task, y: y.task });
} catch (error) {
  console.error(JSON.stringify({ failure: error.name, phase }));
  process.exitCode = 1;
} finally {
  await close();
  lines.close();
}
