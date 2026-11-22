import {
  HTML_PREVIEW_CONTENT_HEIGHT_TYPE,
  HTML_PREVIEW_HEIGHT_TYPE,
  HTML_PREVIEW_MAX_HEIGHT,
  HTML_PREVIEW_MESSAGE_TYPE,
  HTML_PREVIEW_THEME_TOKENS,
} from '@/lib/htmlPreviewFrame';

const messageType = JSON.stringify(HTML_PREVIEW_MESSAGE_TYPE);
const heightType = JSON.stringify(HTML_PREVIEW_HEIGHT_TYPE);
const contentHeightType = JSON.stringify(HTML_PREVIEW_CONTENT_HEIGHT_TYPE);
const themeTokens = JSON.stringify(Object.keys(HTML_PREVIEW_THEME_TOKENS));

// The generated document is untrusted. This frame document (opaque origin,
// sandboxed by the app) prepends a small shell to it: an egress guard, theme
// variables and a height reporter. The reporter only talks to this frame
// document, which clamps the value before relaying it to the app. The content
// never receives a channel to the app window.
//
// Egress: the CSP (frontend/proxy.ts) blocks subresources, connections,
// workers, forms and frame navigation; the sandbox blocks popups and top
// navigation. CSP does not govern WebRTC, so the guard removes its
// constructors from the content window. If the content document is ever
// replaced by anything but this frame's own srcdoc write, the frame tears it
// down. What this proves is pinned by e2e/csp-previews.spec.ts; residual
// limits are listed in docs/ARTIFACT_PRESENTATION_DESIGN.md.
const FRAME_DOCUMENT = `<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
      html, body, #daemon-html-preview-content {
        border: 0;
        height: 100%;
        margin: 0;
        padding: 0;
        width: 100%;
      }
    </style>
  </head>
  <body>
    <iframe id="daemon-html-preview-content" sandbox="allow-scripts"></iframe>
    <script>
      const previewFrame = document.getElementById("daemon-html-preview-content");
      const themeTokens = ${themeTokens};
      const themeValue = /^[-#%(),./\\w\\s]{1,64}$/;
      const guard =
        "<script>(function(){for(var n of ['RTCPeerConnection'," +
        "'webkitRTCPeerConnection','RTCDataChannel','RTCIceTransport'," +
        "'RTCSctpTransport']){try{Object.defineProperty(window,n," +
        "{value:undefined,writable:false,configurable:false});}catch(e){}}})();<\\/script>";
      const stopped =
        "<!doctype html><meta charset=utf-8><p style='font:14px system-ui;" +
        "padding:16px'>This page tried to leave its sandbox and was stopped.</p>";
      // One load is expected per srcdoc write; any other load is the content
      // navigating its frame.
      let expectedLoads = 0;
      previewFrame.addEventListener("load", () => {
        if (expectedLoads > 0) {
          expectedLoads -= 1;
          return;
        }
        expectedLoads = 1;
        previewFrame.srcdoc = stopped;
      });
      const reporter =
        "<script>(function(){var last=0;function post(){" +
        "var d=document.documentElement,b=document.body;" +
        "var h=Math.max(d?d.scrollHeight:0,b?b.scrollHeight:0);" +
        "if(!h||Math.abs(h-last)<2)return;last=h;" +
        "parent.postMessage({type:" + JSON.stringify(${contentHeightType}) + ",height:h},'*');}" +
        "if(window.ResizeObserver){new ResizeObserver(post).observe(document.documentElement);}" +
        "addEventListener('load',post);setTimeout(post,50);setTimeout(post,400);})();<\\/script>";

      function themeStyle(theme) {
        if (!theme || typeof theme !== "object") return "";
        const declarations = [];
        for (const token of themeTokens) {
          const value = theme[token];
          if (typeof value === "string" && themeValue.test(value)) {
            declarations.push(token + ":" + value);
          }
        }
        return declarations.length
          ? "<style>:root{" + declarations.join(";") + "}<\\/style>"
          : "";
      }

      function withShell(content, shell) {
        const doctype = /^\\s*<!doctype[^>]*>/i.exec(content);
        return doctype
          ? doctype[0] + shell + content.slice(doctype[0].length)
          : shell + content;
      }

      window.addEventListener("message", (event) => {
        const payload = event.data;
        if (event.source === previewFrame.contentWindow) {
          if (
            payload &&
            payload.type === ${contentHeightType} &&
            typeof payload.height === "number" &&
            Number.isFinite(payload.height) &&
            payload.height > 0
          ) {
            window.parent.postMessage(
              {
                type: ${heightType},
                height: Math.min(Math.ceil(payload.height), ${HTML_PREVIEW_MAX_HEIGHT}),
              },
              "*",
            );
          }
          return;
        }
        if (
          event.source !== window.parent ||
          !payload ||
          payload.type !== ${messageType} ||
          typeof payload.content !== "string"
        ) {
          return;
        }

        previewFrame.title =
          typeof payload.title === "string" ? payload.title : "HTML Preview";
        expectedLoads = 1;
        previewFrame.srcdoc = withShell(
          payload.content,
          guard + themeStyle(payload.theme) + reporter,
        );
      });
    </script>
  </body>
</html>`;

export function GET() {
  return new Response(FRAME_DOCUMENT, {
    headers: {
      'Cache-Control': 'no-store',
      'Content-Type': 'text/html; charset=utf-8',
      'X-DNS-Prefetch-Control': 'off',
    },
  });
}
