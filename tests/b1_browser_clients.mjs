// Private stdin/stdout JSON only. No screenshots, traces, console contents,
// cookies, credential bodies or exception messages are ever printed.
import readline from "node:readline";
import { createRequire } from "node:module";
const require = createRequire(
  new URL("../frontend/package.json", import.meta.url),
);
const { chromium, request: apiRequest } = require("playwright");
const { expect } = require("@playwright/test");

let browser;
let origin;
const contexts = [];
const pages = [];
const access = [];
const savedCookies = new Map();
let refreshObserver;
let heldReload;
const emit = (value) => process.stdout.write(JSON.stringify(value) + "\n");
const check = (condition, label) => {
  if (!condition) throw Object.assign(new Error(label), { b1: label });
};
const privatePaths = [
  "/conversations",
  "/%63onversations?legacy=1",
  "/users/me/settings",
  "/users/me/entitlements",
  "/users/me/settings%2F",
  "/daemon/conversations",
  "/daemon/users/me/settings",
  "/daemon/users/me/entitlements",
];
// Supplemental paths are cache/edge negatives only, NOT successful lifetime
// reads. URL dot normalization and one encoded separator are exercised by the
// actual installed worker and edge; malformed decoding must never serve A.
const supplementalPaths = [
  "/conversations/fixture.png",
  "/daemon/conversations/fixture.png",
  "/conversations%2Fseparator.png",
  "/daemon/conversations%2Fseparator.png",
  "/conversations/segment/%2e%2e/dot.png",
  "/daemon/conversations/segment/%2e%2e/dot.png",
  "/%E0%A4%A",
  "/daemon/%E0%A4%A",
];
const privateCachePaths = [...privatePaths, ...supplementalPaths];
const unrelatedPaths = [
  "/__b1/unrelated",
  "/conversations-lookalike.png",
  "https://foreign.invalid/unrelated",
  "https://foreign.invalid/conversations",
];
const cachesUsed = ["others", "images", "static-resources", "fonts"];
async function controlled(page, suffix) {
  await page.waitForFunction(
    (suffix) => navigator.serviceWorker.controller?.scriptURL.endsWith(suffix),
    suffix,
  );
}
async function seed(page) {
  await page.evaluate(
    async ({ paths, names, unrelated }) => {
      for (const name of names) {
        const cache = await caches.open(name);
        for (const path of paths)
          await cache.put(path, new Response("FICTIONAL_ACCOUNT_A"));
        for (const path of unrelated)
          await cache.put(path, new Response("UNRELATED_KEEP"));
      }
    },
    { paths: privateCachePaths, names: cachesUsed, unrelated: unrelatedPaths },
  );
}
async function cacheState(page) {
  return page.evaluate(
    async ({ paths, names, unrelated }) => {
      let privateEntries = 0;
      let preserved = 0;
      for (const name of names) {
        const cache = await caches.open(name);
        for (const path of paths) if (await cache.match(path)) privateEntries++;
        for (const path of unrelated)
          if ((await (await cache.match(path))?.text()) === "UNRELATED_KEEP")
            preserved++;
      }
      return {
        privateEntries,
        preserved,
        expectedPreserved: names.length * unrelated.length,
      };
    },
    { paths: privateCachePaths, names: cachesUsed, unrelated: unrelatedPaths },
  );
}

async function refreshOwnership() {
  return refreshObserver.evaluate(async () => {
    const record = JSON.parse(localStorage.getItem("daemon:auth-coordination"));
    const available = await navigator.locks.request(
      "daemon-auth-cookie",
      { mode: "exclusive", ifAvailable: true },
      (lock) => Boolean(lock),
    );
    return {
      status: record?.status,
      kind: record?.kind ?? null,
      attempt: record?.attempt ?? null,
      epoch: record?.epoch ?? null,
      available,
    };
  });
}
async function request(index, path, method = "GET", options = {}) {
  return pages[index].evaluate(
    async ({ path, method, token, body }) => {
      const headers = {};
      if (token) headers.Authorization = "Bearer " + token;
      if (body !== undefined) headers["Content-Type"] = "application/json";
      try {
        const response = await fetch(path, {
          method,
          headers,
          body: body === undefined ? undefined : JSON.stringify(body),
        });
        const text = await response.text();
        let data = null;
        try {
          data = JSON.parse(text);
        } catch {
          /* Non-JSON errors are not published. */
        }
        return {
          status: response.status,
          cache: response.headers.get("cache-control"),
          emptyConversations:
            Array.isArray(data?.conversations) &&
            data.conversations.length === 0,
          code:
            data && typeof data.detail === "object" && data.detail !== null
              ? (data.detail.code ?? null)
              : null,
          keys:
            data && typeof data === "object"
              ? Object.keys(data).filter((k) => !k.includes("token"))
              : [],
          marker: text.includes("FICTIONAL_ACCOUNT_A"),
          opaque:
            text ===
            JSON.stringify({
              error: "Could not reach the authentication service.",
            }),
        };
      } catch {
        return { networkDenied: true };
      }
    },
    {
      path,
      method,
      token: options.anonymous ? null : access[index],
      body: options.body ?? undefined,
    },
  );
}
async function commands(command) {
  const index = command.index ?? 0;
  switch (command.op) {
    case "launch": {
      origin = command.origin;
      browser = await chromium.launch({
        headless: true,
        args: [
          "--disable-background-networking",
          "--disable-component-update",
          "--no-first-run",
          `--ignore-certificate-errors-spki-list=${process.env.B1_TEST_CERT_SPKI}`,
          "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
        ],
      });
      for (let i = 0; i < 2; i++) {
        const context = await browser.newContext({
          baseURL: origin,
          ignoreHTTPSErrors: true,
          viewport: { width: 1280, height: 900 },
        });
        await context.route("**/*", async (route) => {
          const url = new URL(route.request().url());
          if (url.origin !== origin) return route.abort();
          return route.continue();
        });
        const page = await context.newPage();
        page.setDefaultTimeout(20000);
        page.on("response", async (response) => {
          if (!new URL(response.url()).pathname.startsWith("/api/v1/auth/"))
            return;
          try {
            if (!response.ok()) return;
            const data = await response.json();
            if (typeof data.access_token === "string")
              access[i] = data.access_token;
          } catch {
            /* No response diagnostic may contain a credential. */
          }
        });
        contexts.push(context);
        pages.push(page);
      }
      await pages[0].goto("/__b1/passive");
      return { installedChromium: true };
    }
    case "prior": {
      const page = pages[0];
      await seed(page);
      await page.evaluate(async () => {
        await navigator.serviceWorker.register("/__b1/prior-worker.js", {
          scope: "/",
        });
      });
      await controlled(page, "/__b1/prior-worker.js");
      const online = await request(0, "/daemon/conversations", "GET", {
        anonymous: true,
      });
      check(
        online.status === 401 && !online.marker,
        "old-controller-prefixed-online",
      );
      await contexts[0].setOffline(true);
      const offline = await request(0, "/daemon/conversations", "GET", {
        anonymous: true,
      });
      await contexts[0].setOffline(false);
      check(offline.networkDenied === true, "old-controller-prefixed-leak");
      return {
        syntheticPriorController: true,
        prefixOnline401: true,
        prefixOfflineDenied: true,
        ...(await cacheState(page)),
      };
    }
    case "upgrade": {
      const page = pages[0];
      await page.evaluate(async () => {
        await navigator.serviceWorker.register("/sw.js", { scope: "/" });
      });
      await controlled(page, "/sw.js");
      // clientsClaim can precede the other activate listener's async cleanup.
      // Installed Playwright's waitForFunction tests Promise truthiness before
      // resolution. Poll the awaited enumeration, not an async page predicate.
      await expect
        .poll(async () => (await cacheState(page)).privateEntries, {
          timeout: 30_000,
        })
        .toBe(0);
      return { productionController: true, ...(await cacheState(page)) };
    }
    case "setup": {
      const page = pages[0];
      await page.goto("/setup");
      await page.locator("#setup-token").fill(command.token);
      const complete = page.waitForResponse(
        (r) =>
          new URL(r.url()).pathname === "/api/v1/auth/setup" &&
          r.request().method() === "POST",
      );
      await page
        .getByRole("button", { name: "Complete Setup", exact: true })
        .click();
      const response = await complete;
      check(response.status() === 200, "real-ui-setup-status");
      const data = await response.json();
      access[0] = data.access_token;
      check(!("refresh_token" in data), "web-refresh-not-json");
      await page.waitForURL(origin + "/");
      await controlled(page, "/sw.js");
      return {
        setup: true,
        cookie: await cookieProof(0),
        tokenIssued: Boolean(access[0]),
      };
    }
    case "enroll": {
      // Initiation goes through the actual Settings UI; completion uses the
      // actual AuthLanding fields in an independent browser storage partition.
      await pages[0].goto("/settings/devices");
      const started = pages[0].waitForResponse(
        (r) => new URL(r.url()).pathname === "/api/v1/auth/enroll/start",
      );
      await pages[0].getByRole("button", { name: "Add new device" }).click();
      const response = await started;
      check(response.status() === 200, "real-ui-enrollment-start");
      const enrollment = await response.json();
      const page = pages[1];
      await page.goto("/setup");
      await page.locator("#enrollment-pending-id").fill(enrollment.pending_id);
      await page.locator("#enrollment-code").fill(enrollment.code);
      const completed = page.waitForResponse(
        (r) => new URL(r.url()).pathname === "/api/v1/auth/enroll/complete",
      );
      await page
        .getByRole("button", { name: "Complete Enrollment", exact: true })
        .click();
      const result = await completed;
      check(result.status() === 200, "real-ui-enrollment-complete");
      const data = await result.json();
      access[1] = data.access_token;
      check(!("refresh_token" in data), "enrolled-refresh-not-json");
      await page.waitForURL(origin + "/");
      await controlled(page, "/sw.js");
      return {
        enrolled: true,
        cookie: await cookieProof(1),
        distinctAccess: Boolean(access[0] !== access[1]),
      };
    }
    case "fetch":
      return request(index, command.path, command.method, command);
    case "prepare-held-refresh": {
      check(!heldReload, "previous-held-reload-settled");
      const page = pages[0];
      await page.goto("/");
      await page
        .locator('button[aria-controls="account-menu"]')
        .waitFor({ state: "visible" });
      await expect
        .poll(async () =>
          page.evaluate(
            () =>
              JSON.parse(localStorage.getItem("daemon:auth-coordination"))
                ?.status,
          ),
        )
        .toBe("stable");
      if (!refreshObserver || refreshObserver.isClosed()) {
        refreshObserver = await contexts[0].newPage();
        await refreshObserver.goto("/__b1/passive");
      }
      // Epoch/attempt stay in this private process; only comparison booleans
      // cross IPC. The observer has no AuthProvider or credential writer.
      const previous = await refreshOwnership();
      check(
        previous.available && previous.status === "stable",
        "prepared-refresh-stable",
      );
      heldReload = {
        previous,
        headersSeen: false,
        bodyFinished: false,
        status: null,
        failed: false,
      };
      return { prepared: true };
    }
    case "start-held-reload": {
      check(heldReload && !heldReload.completion, "prepared-refresh-required");
      const pending = heldReload;
      pending.completion = pages[0]
        .waitForResponse(
          (response) =>
            new URL(response.url()).pathname === "/api/v1/auth/refresh" &&
            response.request().method() === "POST",
        )
        .then(async (response) => {
          pending.headersSeen = true;
          pending.status = response.status();
          const error = await response.finished();
          pending.bodyFinished = true;
          pending.failed = Boolean(error);
        })
        .catch(() => {
          pending.failed = true;
        });
      pending.navigation = pages[0]
        .reload({ waitUntil: "domcontentloaded" })
        .catch(() => {
          pending.failed = true;
        });
      return { started: true };
    }
    case "inspect-held-refresh": {
      const pending = heldReload;
      await expect
        .poll(async () => (await refreshOwnership()).status, { timeout: 3000 })
        .toBe("pending");
      if (command.phase === "body")
        await expect
          .poll(() => pending.headersSeen, { timeout: 3000 })
          .toBe(true);
      const first = await refreshOwnership();
      // Two real event-loop frames, not a lease or arbitrary sleep. Prove the
      // same live owner remains while the body/headers are held by private IPC.
      await refreshObserver.evaluate(
        () =>
          new Promise((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(resolve)),
          ),
      );
      const second = await refreshOwnership();
      const gate = pages[0].getByTestId("auth-cache-gate");
      await gate.waitFor({ state: "visible", timeout: 3000 });
      return {
        pending: first.status === "pending" && second.status === "pending",
        kindRefresh: first.kind === "refresh" && second.kind === "refresh",
        attemptPresent: Boolean(first.attempt),
        sameAttempt: first.attempt === second.attempt,
        epochUnchanged:
          first.epoch === pending.previous.epoch &&
          second.epoch === pending.previous.epoch,
        lockUnavailable: !first.available && !second.available,
        headersSeen: pending.headersSeen,
        bodyFinished: pending.bodyFinished,
        refreshStatus: pending.status,
        failed: pending.failed,
        gateVisible: await gate.isVisible(),
        protectedWidgetVisible: await pages[0]
          .locator('button[aria-controls="account-menu"]')
          .isVisible(),
      };
    }
    case "finish-held-refresh": {
      const pending = heldReload;
      await pending.completion;
      await pending.navigation;
      await expect
        .poll(async () => (await refreshOwnership()).status)
        .toBe("stable");
      await pages[0]
        .locator('button[aria-controls="account-menu"]')
        .waitFor({ state: "visible" });
      await expect
        .poll(async () => (await refreshOwnership()).available)
        .toBe(true);
      const final = await refreshOwnership();
      heldReload = null;
      return {
        stable: final.status === "stable",
        epochUnchanged: final.epoch === pending.previous.epoch,
        lockAvailable: final.available,
        bodyFinished: pending.bodyFinished,
        refreshStatus: pending.status,
        failed: pending.failed,
        gateVisible: await pages[0].getByTestId("auth-cache-gate").isVisible(),
        protectedWidgetVisible: true,
      };
    }
    case "decode-probes": {
      await seed(pages[index]);
      const observations = [];
      for (const path of supplementalPaths)
        observations.push({ path, ...(await request(index, path)) });
      return { total: supplementalPaths.length, observations };
    }
    case "plan-status": {
      // The mounted account widget reads the real /api/entitlements proxy on
      // mount. Nothing is stubbed: the server answer decides the visible state.
      const page = pages[index];
      await page.goto("/");
      const widget = page.locator('button[aria-controls="account-menu"]');
      await widget.waitFor({ state: "visible" });
      const settledNow = async () => {
        if (
          await widget
            .getByText("Plan unavailable", { exact: true })
            .first()
            .isVisible()
            .catch(() => false)
        )
          return "unavailable";
        if (
          await widget
            .locator("p")
            .filter({ hasText: /^(Free|Pro|Power)$/ })
            .first()
            .isVisible()
            .catch(() => false)
        )
          return "ready";
        return "pending";
      };
      const deadline = Date.now() + 30000;
      let settled = "pending";
      while (Date.now() < deadline) {
        settled = await settledNow();
        if (settled !== "pending") break;
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
      check(settled !== "pending", "plan-status-pending");
      await widget.click();
      const menu = page.locator("#account-menu");
      await menu.waitFor({ state: "visible" });
      const visible = async (locator) =>
        locator
          .first()
          .isVisible()
          .catch(() => false);
      const currentPlan = await visible(
        menu.getByText("Current plan", { exact: true }),
      );
      const freeHeadline = await visible(
        menu.getByText("Free answers things", { exact: true }),
      );
      const serviceUnavailable = await visible(
        menu.getByText("Plan service is unavailable.", { exact: true }),
      );
      const retry = await visible(
        menu.getByRole("button", { name: "Retry", exact: true }),
      );
      return {
        settled,
        currentPlan,
        freeHeadline,
        serviceUnavailable,
        retry,
      };
    }
    case "home-suggestions": {
      // Mounted welcome panel, real backend call, bounded settle only: the
      // fixed wait afterwards is counted by the parent at the edge.
      const page = pages[index];
      await page.goto("/");
      const panel = page.locator(
        'section[aria-label="Suggested from your recent conversations"]',
      );
      await panel.waitFor({ state: "visible" });
      const status = async (name) =>
        page
          .locator(`[data-testid="suggestion-status-${name}"]`)
          .first()
          .isVisible()
          .catch(() => false);
      const deadline = Date.now() + 30000;
      let settled = "pending";
      while (Date.now() < deadline) {
        if (await status("unavailable")) {
          settled = "unavailable";
          break;
        }
        for (const name of ["error", "expired", "generating"]) {
          if (await status(name)) {
            settled = name;
            break;
          }
        }
        if (settled !== "pending") break;
        if ((await panel.locator("ul li").count()) > 0) {
          settled = "rows";
          break;
        }
        if (
          await page
            .locator('[data-testid="personal-off-note"]')
            .first()
            .isVisible()
            .catch(() => false)
        ) {
          settled = "disabled";
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
      check(settled !== "pending", "home-suggestions-pending");
      return {
        settled,
        rows: await panel.locator("ul li").count(),
        retryVisible: await page
          .locator('[data-testid="retry-suggestions"]')
          .first()
          .isVisible()
          .catch(() => false),
        disabledNote: await page
          .locator('[data-testid="personal-off-note"]')
          .first()
          .isVisible()
          .catch(() => false),
        manageLink: await panel
          .locator('a[href="/settings/profile"]')
          .first()
          .isVisible()
          .catch(() => false),
      };
    }
    case "revoke-other": {
      // Explicit native device revocation over the real Next auth proxy. Device
      // ids stay inside the browser child; only counts/status cross IPC.
      const page = pages[index];
      const listed = await page.evaluate(async (token) => {
        const response = await fetch("/api/v1/auth/devices", {
          headers: { Authorization: "Bearer " + token },
        });
        if (response.status !== 200) return { status: response.status };
        const data = await response.json();
        const others = (data.devices ?? [])
          .filter((device) => !device.current && !device.revoked)
          .map((device) => device.id);
        return { status: 200, others, activeBefore: data.devices.length };
      }, access[index]);
      if (listed.status !== 200)
        return { status: listed.status, revoked: false, others: 0 };
      if (!listed.others?.length)
        return { status: listed.status, revoked: false, others: 0 };
      const revoked = await page.evaluate(
        async ({ token, target }) => {
          const response = await fetch("/api/v1/auth/devices/" + target, {
            method: "DELETE",
            headers: { Authorization: "Bearer " + token },
          });
          if (response.status !== 204 && response.status !== 200)
            return { status: response.status };
          const remaining = await fetch("/api/v1/auth/devices", {
            headers: { Authorization: "Bearer " + token },
          });
          const data = remaining.status === 200 ? await remaining.json() : null;
          return {
            status: response.status,
            active: Array.isArray(data?.devices) ? data.devices.length : null,
          };
        },
        { token: access[index], target: listed.others[0] },
      );
      return {
        status: revoked.status,
        revoked: revoked.status === 200 || revoked.status === 204,
        others: listed.others.length,
        activeBefore: listed.activeBefore,
        active: revoked.active ?? null,
      };
    }
    case "save-cookie": {
      const cookie = (await contexts[index].cookies()).find(
        (c) => c.name === "__Host-daemon_refresh",
      );
      check(Boolean(cookie), "cookie-present");
      savedCookies.set(command.label, cookie.value);
      return { saved: true };
    }
    case "probe-old": {
      // Explicit operator-controlled probe, never application automatic retry.
      const client = await apiRequest.newContext({
        baseURL: origin,
        ignoreHTTPSErrors: true,
      });
      try {
        const response = await client.post("/api/v1/auth/refresh", {
          headers: {
            Cookie: "__Host-daemon_refresh=" + savedCookies.get(command.label),
            Origin: origin,
            "Sec-Fetch-Site": "same-origin",
          },
        });
        // A real refusal clears the refresh cookie in the response headers; the
        // browser cookie jar is not inspected and no cookie value is published.
        const cookieClear = response
          .headersArray()
          .filter((h) => h.name.toLowerCase() === "set-cookie")
          .some((h) => {
            const value = h.value.toLowerCase();
            return (
              value.startsWith("__host-daemon_refresh=") &&
              (value.includes("max-age=0") || value.includes("01 jan 1970"))
            );
          });
        let sameSuccessor = false;
        if (response.status() === 200) {
          const data = await response.json();
          const prior = savedCookies.get(command.resultLabel);
          sameSuccessor = prior ? prior === data.access_token : true;
          if (command.resultLabel)
            savedCookies.set(command.resultLabel, data.access_token);
          if (command.captureCookieLabel) {
            const header = response
              .headersArray()
              .find(
                (h) =>
                  h.name.toLowerCase() === "set-cookie" &&
                  h.value.startsWith("__Host-daemon_refresh="),
              );
            check(Boolean(header), "probe-successor-cookie");
            savedCookies.set(
              command.captureCookieLabel,
              header.value.split(";")[0].slice("__Host-daemon_refresh=".length),
            );
          }
        }
        return { status: response.status(), sameSuccessor, cookieClear };
      } finally {
        await client.dispose();
      }
    }
    case "csrf": {
      const cookie = (await contexts[index].cookies()).find(
        (c) => c.name === "__Host-daemon_refresh",
      );
      const client = await apiRequest.newContext({
        baseURL: origin,
        ignoreHTTPSErrors: true,
      });
      try {
        const response = await client.post("/api/v1/auth/refresh", {
          headers: {
            Cookie: "__Host-daemon_refresh=" + cookie.value,
            Origin: "https://foreign.invalid",
            "Sec-Fetch-Site": "cross-site",
          },
        });
        return { status: response.status() };
      } finally {
        await client.dispose();
      }
    }
    case "headers": {
      const response = await contexts[index].request.get(
        "/api/v1/auth/__b1_headers",
      );
      return {
        status: response.status(),
        count: response
          .headersArray()
          .filter((h) => h.name.toLowerCase() === "set-cookie").length,
      };
    }
    case "reload": {
      // The prior stable record survives reload until hydration starts the
      // next writer. Correlate with this refresh response before reading state.
      const refreshed = pages[index]
        .waitForResponse(
          (response) =>
            new URL(response.url()).pathname === "/api/v1/auth/refresh" &&
            response.request().method() === "POST",
          { timeout: 25000 },
        )
        .catch(() => null);
      let navigated = true;
      try {
        await pages[index].reload();
      } catch {
        // A concurrent application navigation can interrupt the reload; the
        // settling state below is still the evidence, never an exception text.
        navigated = false;
      }
      const response = await refreshed;
      if (response) await response.finished().catch(() => null);
      const refreshStatus = response?.status() ?? null;
      const deadline = Date.now() + 25000;
      let status = null;
      let path = null;
      while (Date.now() < deadline) {
        const view = await pages[index]
          .evaluate(() => {
            const raw = localStorage.getItem("daemon:auth-coordination");
            let value = "absent";
            if (raw) {
              try {
                value = JSON.parse(raw).status ?? "malformed";
              } catch {
                value = "invalid";
              }
            }
            return { status: value, path: location.pathname };
          })
          .catch(() => null);
        if (view) {
          status = view.status;
          path = view.path;
          if (
            status === "uncertain" ||
            (refreshStatus === 200 && status === "stable")
          )
            break;
        }
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
      return { status, path, navigated, refreshStatus };
    }
    case "reenroll": {
      const pending = await pages[1].evaluate(async (token) => {
        const response = await fetch("/api/v1/auth/enroll/start", {
          method: "POST",
          headers: { Authorization: "Bearer " + token },
        });
        if (response.status !== 200) throw new Error("enrollment");
        return response.json();
      }, access[1]);
      await pages[0].goto("/setup");
      await pages[0].locator("#enrollment-pending-id").fill(pending.pending_id);
      await pages[0].locator("#enrollment-code").fill(pending.code);
      const completed = pages[0].waitForResponse(
        (r) => new URL(r.url()).pathname === "/api/v1/auth/enroll/complete",
      );
      await pages[0]
        .getByRole("button", { name: "Complete Enrollment", exact: true })
        .click();
      const response = await completed;
      check(response.status() === 200, "explicit-reenrollment");
      access[0] = (await response.json()).access_token;
      await pages[0].waitForURL(origin + "/");
      return { enrolled: true };
    }
    case "cache-offline": {
      let stage = "seed";
      const results = [];
      try {
        await seed(pages[index]);
        stage = "set-offline";
        await contexts[index].setOffline(true);
        for (const path of privateCachePaths) {
          stage = `fetch-${privateCachePaths.indexOf(path)}`;
          results.push(await request(index, path));
        }
        stage = "enumerate";
        // Restoring connectivity can navigate the application document. Inspect
        // Cache Storage while still offline, in the document that made the
        // denied requests; finally restores networking after this proof.
        const stored = await cacheState(pages[index]);
        return {
          total: privateCachePaths.length,
          denied: results.filter((r) => r.networkDenied === true && !r.marker)
            .length,
          observations: results.map((r, i) => ({
            path: privateCachePaths[i],
            status: r.status ?? null,
            networkDenied: r.networkDenied === true,
            marker: r.marker === true,
          })),
          ...stored,
        };
      } catch (error) {
        // Only a fixed stage label crosses IPC; never an exception/message or
        // Playwright's call log, which may include credential-bearing input.
        const message = String(error?.message ?? "");
        return {
          diagnosticFailure: stage,
          contextDestroyed: /Execution context was destroyed|navigation/.test(
            message,
          ),
          cacheFailure: /Cache|cache|storage/.test(message),
          undefinedValue: /undefined|null/.test(message),
          timeout: /Timeout|timeout/.test(message),
          observations: results.map((r, i) => ({
            path: privateCachePaths[i],
            status: r?.status ?? null,
            networkDenied: r?.networkDenied === true,
            marker: r?.marker === true,
          })),
        };
      } finally {
        await contexts[index].setOffline(false);
      }
    }
    case "lifetime": {
      // B is a new credential lifetime for the same singleton, NOT another UUID.
      const pending = await pages[1].evaluate(async (token) => {
        const response = await fetch("/api/v1/auth/enroll/start", {
          method: "POST",
          headers: { Authorization: "Bearer " + token },
        });
        if (response.status !== 200) throw new Error("enrollment");
        return response.json();
      }, access[1]);
      await seed(pages[0]);
      // Use the real Settings logout action if available through its shared UI.
      const logout = await request(0, "/api/v1/auth/logout", "POST");
      check(logout.status === 200 || logout.status === 204, "real-logout");
      // Fresh document runs normal refresh rejection and explicit enrollment.
      await pages[0].goto("/setup");
      await pages[0].locator("#enrollment-pending-id").fill(pending.pending_id);
      await pages[0].locator("#enrollment-code").fill(pending.code);
      const completed = pages[0].waitForResponse(
        (r) => new URL(r.url()).pathname === "/api/v1/auth/enroll/complete",
      );
      await pages[0]
        .getByRole("button", { name: "Complete Enrollment", exact: true })
        .click();
      check((await completed).status() === 200, "new-lifetime-enrollment");
      await pages[0].waitForURL(origin + "/");
      const results = [];
      for (const path of privatePaths.filter((p) => p.startsWith("/daemon/")))
        results.push(await request(0, path));
      check(
        results.every((r) => r.status === 200 && !r.marker),
        "new-lifetime-stale-marker",
      );
      return {
        singletonCredentialLifetime: true,
        distinctAccountSwitch: false,
        realReads: results.length,
      };
    }
    case "stop": {
      await browser?.close();
      return { stopped: true };
    }
    default:
      throw new Error("unknown-command");
  }
}
async function cookieProof(index) {
  const cookie = (await contexts[index].cookies()).find(
    (c) => c.name === "__Host-daemon_refresh",
  );
  const visible = await pages[index].evaluate(() =>
    document.cookie.includes("daemon_refresh"),
  );
  return {
    secure: cookie?.secure,
    httpOnly: cookie?.httpOnly,
    sameSite: cookie?.sameSite,
    hostOnly: cookie?.domain === "127.0.0.1",
    path: cookie?.path,
    hidden: !visible,
  };
}
for await (const line of readline.createInterface({ input: process.stdin })) {
  let command;
  try {
    command = JSON.parse(line);
    const result = await commands(command);
    emit({ id: command.id, ok: true, result });
    if (command.op === "stop") break;
  } catch (error) {
    // No Playwright call log/stack, DOM snapshot or request object crosses IPC.
    emit({
      id: command?.id,
      ok: false,
      stage: command?.op ?? "decode",
      reason: error.b1 ?? error.name,
    });
  }
}
await browser?.close();
