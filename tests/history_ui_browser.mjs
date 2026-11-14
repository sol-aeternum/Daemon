// Real page, normal setup/enrollment and actual private responses; no fulfill/mock.
import { chromium } from "../frontend/node_modules/playwright/index.mjs";
import { createInterface } from "node:readline";
import assert from "node:assert/strict";

const lines = createInterface({ input: process.stdin });
const frames = lines[Symbol.asyncIterator]();
const { ui, backend, bootstrap, outcome } = JSON.parse(
  (await frames.next()).value,
);
const browser = await chromium.launch({ headless: true });
let closing;
const closeBrowser = () => (closing ??= browser.close());
process.once("SIGTERM", async () => {
  await closeBrowser();
  lines.close();
  process.exit(143);
});
const destinations = new Set([ui, backend]);
const unexpected = [];
const calls = [];
const statuses = [];
let phase = "browser-startup";
async function context() {
  const value = await browser.newContext();
  value.on("response", (response) => {
    const url = new URL(response.url());
    if (destinations.has(url.origin) && (url.origin === backend || url.pathname.startsWith("/api/"))) {
      statuses.push({path: url.pathname, status: response.status()});
    }
  });
  await value.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (!destinations.has(url.origin)) {
      unexpected.push(url.origin);
      return route.abort("blockedbyclient");
    }
    // Record only method/path; never bearer, cookie, bootstrap or enroll codes.
    calls.push([request.method(), url.origin, url.pathname]);
    return route.continue();
  });
  return value;
}
try {
  const a = await context();
  const first = await a.newPage();
  phase = "normal-setup-page";
  await first.goto(ui + "/setup");
  await first.getByPlaceholder("Paste your setup token here").fill(bootstrap);
  const issued = first.waitForResponse(
    (r) => new URL(r.url()).pathname === "/api/v1/auth/setup",
  );
  await first.getByRole("button", { name: "Complete Setup" }).click();
  const setup = await issued;
  assert.equal(setup.status(), 200);
  // Observe the first device's actual issuer response only for its own
  // authenticated enrollment-start request. Never install or copy credentials.
  const firstToken = (await setup.json()).access_token;
  phase = "first-device-enrollment-start";
  const enrollment = await first.evaluate(async (token) => {
    const response = await fetch("/api/v1/auth/enroll/start", {
      method: "POST",
      headers: { Authorization: "Bearer " + token },
    });
    if (!response.ok) throw Error("Enrollment start refused");
    return response.json();
  }, firstToken);
  phase = "actual-page-send";
  await first
    .getByRole("textbox", { name: "Message Daemon" })
    .fill("synthetic text");
  const chatRequest = first.waitForRequest(
    (r) => new URL(r.url()).pathname === "/api/chat" && r.method() === "POST",
  );
  await first.getByRole("button", { name: "Send message" }).click();
  const sent = await chatRequest;
  const body = sent.postDataJSON();
  // The browser owns the key in JSON; the unchanged Next bridge copies it
  // into the backend Idempotency-Key header. Do not invent a second key.
  const key = body.idempotency_key;
  assert.match(key ?? "", /^[A-Za-z0-9._:-]{1,128}$/);
  assert.equal(body.model, "auto");
  assert.equal(body.answer_detail, "balanced");
  assert.equal(body.research_effort, "auto");
  phase = "first-page-saved-partial";
  await first
    .getByLabel("Conversation messages")
    .getByText("original partial", { exact: true })
    .waitFor({ timeout: 60000 });
  const conversation = new URL(first.url()).searchParams.get("id");
  assert.ok(conversation);
  assert.equal(calls.filter(([method,,path]) => method === "POST" && path.endsWith("/cancel")).length, 0);
  console.log(
    JSON.stringify({
      stage: "first-partial",
      conversation,
      key,
      partial: "original partial",
    }),
  );
  // Parent independently verifies the committed SQL prefix and the exact
  // browser-generated key while this first page is still attached.
  assert.equal((await frames.next()).value, "first-verified");
  await a.close(); // Detach; deliberately no Stop.
  const b = await context();
  const second = await b.newPage();
  phase = "second-device-normal-enrollment";
  await second.goto(ui + "/setup");
  await second
    .getByLabel("Enrollment Link or Token")
    .fill(enrollment.qr_payload);
  await second.getByRole("button", { name: "Complete Enrollment" }).click();
  await second.getByRole("textbox", { name: "Message Daemon" }).waitFor();
  phase = "second-device-reopen";
  await second.goto(ui + "/?id=" + conversation);
  await second
    .getByLabel("Conversation messages")
    .getByText("original partial", { exact: true })
    .waitFor({ timeout: 30000 });
  // Trigger the normal visibility/focus and covered-range interval rather than
  // injecting history or manually supplying a cursor.
  phase = "normal-covered-range-refresh";
  const refreshed = await second.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === "/conversations/" + conversation &&
      new URL(r.url()).searchParams.has("through"),
    { timeout: 40000 },
  );
  assert.equal(refreshed.status(), 200);
  const saved = await refreshed.json();
  const task = saved.latest_task.id;
  assert.equal(saved.messages.at(-1).content, "original partial");
  statuses.push({
    path: "/conversations/" + conversation,
    status: refreshed.status(),
    task,
    native: saved.latest_task.status,
  });
  assert.equal(calls.filter(([method,,path]) => method === "POST" && path.endsWith("/cancel")).length, 0);
  console.log(
    JSON.stringify({ stage: "second-partial", conversation, key, task }),
  );
  if (outcome === "stop") {
    phase = "real-stop-and-terminal-confirmation";
    const confirmed = second.waitForResponse(
      async (response) => {
        const url = new URL(response.url());
        if (
          url.origin !== backend ||
          url.pathname !== "/tasks/" + task ||
          response.status() !== 200
        )
          return false;
        return (await response.json()).status === "cancelled";
      },
      { timeout: 20000 },
    );
    await second.getByRole("button", { name: "Stop generating" }).click();
    const terminal = await confirmed;
    const body = await terminal.json();
    assert.equal(body.id, task);
    assert.equal(body.status, "cancelled");
    statuses.push({
      path: "/tasks/" + task,
      status: terminal.status(),
      task,
      native: body.status,
    });
    await second
      .getByLabel("Conversation messages")
      .getByText(
        /Daemon stopped local work; provider activity or charges may continue\./,
      )
      .waitFor({ timeout: 20000 });
    await second
      .getByLabel("Conversation messages")
      .getByText(/Any saved partial answer is incomplete\./)
      .waitFor();
  } else {
    phase = "real-completion-readback";
    assert.equal((await frames.next()).value, "complete");
    await second
      .getByLabel("Conversation messages")
      .getByText("original partial and final", { exact: true })
      .waitFor({ timeout: 20000 });
  }
  assert.deepEqual(unexpected, []);
  assert.equal(
    calls.filter(
      ([method, , path]) => method === "POST" && path === "/api/chat",
    ).length,
    1,
  );
  await b.close();
  console.log(
    JSON.stringify({
      stage: "finished",
      actual_page: true,
      distinct_issued_devices: true,
      detached_without_stop: true,
      normal_range_refresh: true,
      outcome,
      statuses,
    }),
  );
} catch (error) {
  // Only a fixed failure class; credential-bearing request/response data stays out.
  console.error(JSON.stringify({failure: error.name, phase, statuses}));
  process.exitCode = 1;
} finally {
  await closeBrowser();
  lines.close();
}
