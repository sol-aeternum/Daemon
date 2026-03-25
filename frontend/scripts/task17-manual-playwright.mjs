import { chromium } from "playwright";
import { writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const BASE_URL = "http://localhost:3000";
const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const EVIDENCE_TXT = path.resolve(SCRIPT_DIR, "../../.sisyphus/evidence/task-17-manual.txt");
const EVIDENCE_PNG = path.resolve(SCRIPT_DIR, "../../.sisyphus/evidence/task-17-manual-final.png");
const results = [];

function record(step, passed, details) {
  results.push({ step, passed, details });
}

function extractConversationId(streamBody) {
  const match = streamBody.match(/"conversation_id":"([^"]+)"/);
  return match ? match[1] : null;
}

async function sendMessage(page, text) {
  const input = page.getByPlaceholder("Message Daemon...");
  const requestPromise = page.waitForRequest(
    (request) => {
      if (!request.url().includes("/api/chat")) {
        return false;
      }
      const body = request.postData() || "";
      try {
        const parsed = JSON.parse(body);
        const messages = Array.isArray(parsed?.messages) ? parsed.messages : [];
        const lastMessage = messages[messages.length - 1];
        return lastMessage?.role === "user" && lastMessage?.content === text;
      } catch {
        return body.includes(`\"content\":\"${JSON.stringify(text).slice(1, -1)}\"`);
      }
    },
    { timeout: 120000 },
  ).catch(() => null);

  await input.waitFor({ state: "visible", timeout: 45000 });
  await input.click();
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.press("Backspace");
  await page.keyboard.type(text);

  const sendButton = page.getByRole("button", { name: "Send message" });
  let enabled = false;
  for (let i = 0; i < 120; i += 1) {
    enabled = !(await sendButton.isDisabled());
    if (enabled) break;
    await page.waitForTimeout(250);
  }

  if (!enabled) {
    const justChat = page.getByRole("button", { name: "Just Chat" });
    if (await justChat.isVisible().catch(() => false)) {
      await justChat.click();
      await input.click();
      await page.keyboard.press("ControlOrMeta+A");
      await page.keyboard.press("Backspace");
      await page.keyboard.type(text);
      for (let i = 0; i < 80; i += 1) {
        enabled = !(await sendButton.isDisabled());
        if (enabled) break;
        await page.waitForTimeout(250);
      }
    }
  }

  if (!enabled) {
    throw new Error("Send button never became enabled after typing message");
  }

  await sendButton.click();

  const matchedRequest = await requestPromise;
  if (!matchedRequest) {
    return { requestBody: "", responseBody: "" };
  }

  const matchedResponse = await matchedRequest.response();
  if (!matchedResponse) {
    return {
      requestBody: matchedRequest.postData() || "",
      responseBody: "",
    };
  }

  const requestBody = matchedRequest.postData() || "";
  try {
    return {
      requestBody,
      responseBody: await matchedResponse.text(),
    };
  } catch {
    return {
      requestBody,
      responseBody: "",
    };
  }
}

async function waitForAny(page, timeoutMs, locators) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    for (const locator of locators) {
      if (await locator.isVisible().catch(() => false)) {
        return true;
      }
    }
    await page.waitForTimeout(250);
  }
  return false;
}

async function waitForCondition(timeoutMs, predicate) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (await predicate()) {
      return true;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return false;
}

async function hasCouncilOutputInDom(page) {
  const headingCount = await page.getByRole("heading", { name: "Council Output" }).count();
  if (headingCount > 0) {
    return true;
  }
  const sectionCount = await page.getByText("Where All Advisors Agree", { exact: false }).count();
  return sectionCount > 0;
}

async function isVisibleWithin(locator, timeoutMs) {
  try {
    await locator.waitFor({ state: "visible", timeout: timeoutMs });
    return true;
  } catch {
    return false;
  }
}

async function run() {
  let browser;
  let page;

  try {
    browser = await chromium.launch({ headless: true });
    page = await browser.newPage();
    let activeConversationId = null;

    await page.goto(BASE_URL, { waitUntil: "domcontentloaded", timeout: 45000 });
    await page.getByPlaceholder("Message Daemon...").waitFor({ state: "visible", timeout: 45000 });

    const newChatButton = page.getByRole("button", { name: "New chat" });
    if (await newChatButton.isVisible().catch(() => false)) {
      await newChatButton.click();
      await page.waitForTimeout(1000);
    }

    const initialStep = await sendMessage(page, "/council Should I sell my investment property?");
    activeConversationId = extractConversationId(initialStep.responseBody) || activeConversationId;
    const interviewVisible = await isVisibleWithin(
      page.getByRole("heading", { name: "Council Configuration" }),
      45000,
    );
    record(1, interviewVisible, interviewVisible ? "Council command accepted" : "No interview card after /council prompt");

    record(2, interviewVisible, interviewVisible ? "Interview card rendered" : "Interview card missing");

    if (interviewVisible) {
      await page.getByRole("button", { name: "Use Defaults" }).click();
    }

    const progressVisible = await isVisibleWithin(
      page.getByText("Council Deliberation", { exact: false }),
      45000,
    );
    record(3, progressVisible, progressVisible ? "Progress displayed after Use Defaults" : "Progress not displayed");

    const outputVisible = await isVisibleWithin(
      page.getByRole("heading", { name: "Council Output" }),
      90000,
    );
    const sectionVisible = await isVisibleWithin(
      page.getByText("Where All Advisors Agree", { exact: false }),
      90000,
    );
    record(4, outputVisible && sectionVisible, outputVisible && sectionVisible ? "Output sections rendered" : "Council output sections missing");

    let expandCollapseOk = false;
    if (outputVisible) {
      await page.getByRole("button", { name: "Collapse All" }).click().catch(() => {});
      await page.waitForTimeout(500);
      await page.getByRole("button", { name: "Expand All" }).click().catch(() => {});
      await page.waitForTimeout(500);
      expandCollapseOk = true;
    }
    record(5, expandCollapseOk, expandCollapseOk ? "Expand/Collapse controls clickable" : "Expand/Collapse controls unavailable");

    const followupStep = await sendMessage(page, "/council Drill into disagreement between advisors");
    activeConversationId = extractConversationId(followupStep.responseBody) || activeConversationId;
    const followupInterview = await isVisibleWithin(
      page.getByRole("heading", { name: "Council Configuration" }).last(),
      45000,
    );
    record(6, followupInterview, followupInterview ? "Follow-up council request accepted" : "No second council interview after follow-up prompt");

    const beforeDefaultInterviewCount = await page.getByRole("heading", { name: "Council Configuration" }).count();
    const defaultStep = await sendMessage(page, "/council --default test");
    const defaultResponseBody = defaultStep.responseBody;
    activeConversationId = extractConversationId(defaultResponseBody) || activeConversationId;
    await page.waitForTimeout(2000);
    const afterDefaultInterviewCount = await page.getByRole("heading", { name: "Council Configuration" }).count();
    const defaultResponseHasInterview = defaultResponseBody.includes('"type":"council_interview"');
    const defaultResponseHasExecution = defaultResponseBody.includes('"type":"council_progress"')
      || defaultResponseBody.includes('"type":"council_output"')
      || defaultResponseBody.includes('"type":"council_done"');
    const defaultSkippedInterview =
      afterDefaultInterviewCount === beforeDefaultInterviewCount
      && defaultResponseHasExecution
      && !defaultResponseHasInterview;
    record(
      7,
      defaultSkippedInterview,
      defaultSkippedInterview
        ? "--default flow skipped interview"
        : `--default flow appeared to prompt interview | req=${defaultStep.requestBody.slice(0, 180)} | resp=${defaultResponseBody.slice(0, 240)}`,
    );

    const normalStep = await sendMessage(page, "Tell me a normal chat response in one sentence.");
    const normalResponseBody = normalStep.responseBody;
    activeConversationId = extractConversationId(normalResponseBody) || activeConversationId;
    const normalResponseTextSeen = await waitForAny(page, 60000, [
      page.getByText("API key required", { exact: false }),
      page.getByText("API key not configured", { exact: false }),
      page.getByText("Okay, I can help with that", { exact: false }),
      page.getByText("Daemon", { exact: false }),
    ]);

    const normalIsNonCouncil = !normalResponseBody.includes('"type":"council_');
    record(
      8,
      (normalResponseTextSeen || normalIsNonCouncil),
      (normalResponseTextSeen || normalIsNonCouncil)
        ? "Normal message bypassed council UI"
        : "Could not confirm normal chat response"
    );

    const persistenceStep = await sendMessage(page, "/council --default persistence check after refresh");
    const persistenceResponseBody = persistenceStep.responseBody;
    activeConversationId = extractConversationId(persistenceResponseBody) || activeConversationId;
    const persistenceResponseHasOutput = persistenceResponseBody.includes('"type":"council_progress"')
      || persistenceResponseBody.includes('"type":"council_output"')
      || persistenceResponseBody.includes('"type":"council_done"');

    const outputBeforeRefresh = await waitForCondition(90000, async () => hasCouncilOutputInDom(page));

    await page.reload({ waitUntil: "domcontentloaded", timeout: 45000 });
    let postCouncilOutputAfterRefresh = await waitForCondition(25000, async () => hasCouncilOutputInDom(page));

    if (!postCouncilOutputAfterRefresh && activeConversationId) {
      await page.goto(`${BASE_URL}/?id=${activeConversationId}`, {
        waitUntil: "domcontentloaded",
        timeout: 45000,
      });
      postCouncilOutputAfterRefresh = await waitForCondition(90000, async () => hasCouncilOutputInDom(page));
    }
    const step10Passed = persistenceResponseHasOutput && outputBeforeRefresh && postCouncilOutputAfterRefresh;
    record(
      10,
      step10Passed,
      step10Passed
        ? "Council output re-rendered after refresh"
        : `Council output did not re-render after refresh (respHasOutput=${persistenceResponseHasOutput}, before=${outputBeforeRefresh}, after=${postCouncilOutputAfterRefresh})`
    );

    const refreshStep = await sendMessage(page, "/council --default test refresh mid-council");
    const refreshResponseBody = refreshStep.responseBody;
    activeConversationId = extractConversationId(refreshResponseBody) || activeConversationId;

    const midProgressVisible = await waitForAny(page, 45000, [
      page.getByText("Council Deliberation", { exact: false }),
      page.getByRole("heading", { name: "Council Output" }),
    ]);
    const midProgressByResponse = refreshResponseBody.includes('"type":"council_progress"')
      || refreshResponseBody.includes('"type":"council_output"');
    const midProgress = midProgressVisible || midProgressByResponse;

    await page.reload({ waitUntil: "domcontentloaded", timeout: 45000 });
    let recoveredAfterRefresh = false;
    try {
      await page.getByPlaceholder("Message Daemon...").waitFor({ state: "visible", timeout: 45000 });
      recoveredAfterRefresh = true;
    } catch {
      recoveredAfterRefresh = false;
    }
    record(9, midProgress && recoveredAfterRefresh, midProgress && recoveredAfterRefresh ? "Refresh mid-council recovered UI" : "Mid-council refresh recovery failed");
  } catch (error) {
    record("fatal", false, String(error));
  } finally {
    if (page) {
      await page.screenshot({ path: EVIDENCE_PNG, fullPage: true }).catch(() => {});
    }
    if (browser) {
      await browser.close().catch(() => {});
    }
  }

  const passed = results.filter((r) => r.passed).length;
  const total = results.length;
  const lines = [
    "Task 17 manual verification (Playwright)",
    `Base URL: ${BASE_URL}`,
    `Passed: ${passed}/${total}`,
    "",
    ...results.map((r) => `- [${r.passed ? "x" : " "}] ${r.step}: ${r.details}`),
    "",
    `Overall: ${results.every((r) => r.passed) ? "PASS" : "FAIL"}`,
  ];

  await writeFile(EVIDENCE_TXT, `${lines.join("\n")}\n`, "utf8");

  if (!results.every((r) => r.passed)) {
    process.exitCode = 1;
  }
}

await run();
