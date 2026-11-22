'use client';

import { useSyncExternalStore } from 'react';
import { VideoPlayer } from '../../../components/VideoPlayer';
import { DocxPreview } from '../../../src/components/previews/DocxPreview';
import { HtmlPreview } from '../../../src/components/previews/HtmlPreview';
import { SandboxedHtmlFrame } from '../../../components/chat/SandboxedHtmlFrame';

const DOCX_BASE64 =
  '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';

const HTML_CONTENT = `<!doctype html>
<style>#run-script { color: rgb(0, 128, 0); }</style>
<button id="run-script" type="button">Run script</button>
<output id="script-result">idle</output>
<script>
  document.getElementById("run-script").addEventListener("click", () => {
    document.getElementById("script-result").textContent = "ran";
  });
</script>`;

const SIZED_CONTENT = `<!doctype html>
<div id="tall" style="height: 700px">tall</div>
<img id="remote" src="https://example.com/pixel.png" alt="">
<script>
  const image = document.getElementById("remote");
  image.addEventListener("error", () => { document.body.dataset.remote = "blocked"; });
  image.addEventListener("load", () => { document.body.dataset.remote = "loaded"; });
</script>`;

// Every egress route generated content could try, aimed at a local listener
// the browser test runs (?probe=<port>). Results land on body data
// attributes; the listener's hit count is the real evidence.
function egressProbe(port: string): string {
  const base = `http://127.0.0.1:${port}/__egress/`;
  return `<!doctype html>
<body>
<p>probe</p>
<script>
  const base = ${JSON.stringify(base)};
  const mark = (name, value) => { document.body.dataset[name] = value; };
  const attempt = (name, run) => { try { run(); } catch (e) { mark(name, "threw"); } };
  attempt("fetch", () => fetch(base + "fetch").then(() => mark("fetch", "open"), () => mark("fetch", "blocked")));
  attempt("xhr", () => { const x = new XMLHttpRequest(); x.onload = () => mark("xhr", "open"); x.onerror = () => mark("xhr", "blocked"); x.open("GET", base + "xhr"); x.send(); });
  attempt("socket", () => { const w = new WebSocket(base.replace("http", "ws") + "socket"); w.onopen = () => mark("socket", "open"); w.onerror = () => mark("socket", "blocked"); });
  attempt("events", () => { const e = new EventSource(base + "events"); e.onopen = () => mark("events", "open"); e.onerror = () => { mark("events", "blocked"); e.close(); }; });
  attempt("beacon", () => mark("beacon", String(navigator.sendBeacon(base + "beacon", "x"))));
  attempt("image", () => { const i = new Image(); i.onload = () => mark("image", "open"); i.onerror = () => mark("image", "blocked"); i.src = base + "image"; });
  attempt("css", () => { const d = document.createElement("div"); d.style.backgroundImage = "url(" + base + "css)"; d.style.width = d.style.height = "4px"; document.body.append(d); });
  attempt("prefetch", () => { const l = document.createElement("link"); l.rel = "prefetch"; l.href = base + "prefetch"; document.head.append(l); });
  attempt("script", () => { const s = document.createElement("script"); s.src = base + "script"; document.head.append(s); });
  attempt("worker", () => { new Worker(URL.createObjectURL(new Blob(["fetch(" + JSON.stringify(base + "worker") + ")"]))); mark("worker", "created"); });
  attempt("frame", () => { const f = document.createElement("iframe"); f.src = base + "frame"; document.body.append(f); });
  attempt("form", () => { const f = document.createElement("form"); f.method = "post"; f.action = base + "form"; document.body.append(f); f.submit(); });
  attempt("popup", () => mark("popup", window.open(base + "popup") ? "open" : "blocked"));
  attempt("top", () => { top.location.href = base + "top"; });
  mark("webrtc", typeof RTCPeerConnection === "undefined" && typeof webkitRTCPeerConnection === "undefined" ? "unavailable" : "available");
  mark("done", "1");
</script>
</body>`;
}

function navigationProbe(port: string, how: 'location' | 'refresh'): string {
  const target = `http://127.0.0.1:${port}/__egress/navigate-${how}`;
  return how === 'location'
    ? `<!doctype html><p>leaving</p><script>setTimeout(() => { location.href = ${JSON.stringify(target)}; }, 100);</script>`
    : `<!doctype html><meta http-equiv="refresh" content="0;url=${target}"><p>leaving</p>`;
}

const noSubscription = () => () => {};

function EgressProbes() {
  const search = useSyncExternalStore(
    noSubscription,
    () => window.location.search,
    () => '',
  );
  const value = new URLSearchParams(search).get('probe');
  const port = value && /^\d{2,5}$/.test(value) ? value : null;
  if (!port) return null;
  return (
    <section data-testid="egress-probes">
      <SandboxedHtmlFrame content={egressProbe(port)} title="Egress probe" />
      <SandboxedHtmlFrame
        content={navigationProbe(port, 'location')}
        title="Navigation probe"
      />
      <SandboxedHtmlFrame
        content={navigationProbe(port, 'refresh')}
        title="Refresh probe"
      />
    </section>
  );
}

const VIDEO_DATA_URL =
  'data:video/webm;base64,GkXfo59ChoEBQveBAULygQRC84EIQoKEd2VibUKHgQJChYECGFOAZwH/////////EU2bdKtNu4tTq4QVSalmU6yBoU27i1OrhBZUrmtTrIHNTbuMU6uEElTDZ1OsggET7AEAAAAAAABoAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAVSalmpyrXsYMPQkBNgI1MYXZmNjAuMTYuMTAwV0GNTGF2ZjYwLjE2LjEwMBZUrmvBrgEAAAAAAAA414EBc8WIK1B6ze09/aicgQAitZyDdW5kiIEAhoVWX1ZQOYOBASPjg4QCYloA4ImwgRC6gRCagQISVMNn3HNzoGPAgGfImkWjh0VOQ09ERVJEh41MYXZmNjAuMTYuMTAwc3O2Y8CLY8WIK1B6ze09/ahnyKVFo4dFTkNPREVSRIeYTGF2YzYwLjMxLjEwMiBsaWJ2cHgtdnA5H0O2dfnngQCjoIEAAICCSYNCAADwAPYAOCQcGEoAADBgAAAQv//9SIwAo5OBACgAhgBAkpwAUAAAAyAAAEJAo5OBAFAAhgBAkpwATuAAAyAAAEJAo5OBAHgAhgBAkpwAUAAAAyAAAEJAo5OBAKAAhgBAkpwATUAAAyAAAEJA';

export default function Page() {
  return (
    <main>
      <section data-testid="docx-preview">
        <DocxPreview
          content={`data:application/vnd.openxmlformats-officedocument.wordprocessingml.document;base64,${DOCX_BASE64}`}
          filename="csp-preview.docx"
        />
      </section>

      <section data-testid="html-preview">
        <HtmlPreview content={HTML_CONTENT} title="Interactive HTML" />
      </section>

      <section data-testid="sized-preview">
        <SandboxedHtmlFrame
          content={SIZED_CONTENT}
          title="Sized HTML"
          autoHeight
        />
      </section>

      <EgressProbes />

      <section data-testid="video-preview">
        <VideoPlayer src={VIDEO_DATA_URL} filename="inline.webm" />
      </section>
    </main>
  );
}
