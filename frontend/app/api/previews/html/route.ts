import { HTML_PREVIEW_MESSAGE_TYPE } from '@/lib/htmlPreviewFrame';

const messageType = JSON.stringify(HTML_PREVIEW_MESSAGE_TYPE);

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
      window.addEventListener("message", (event) => {
        const payload = event.data;
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
        previewFrame.srcdoc = payload.content;
      });
    </script>
  </body>
</html>`;

export function GET() {
  return new Response(FRAME_DOCUMENT, {
    headers: {
      'Cache-Control': 'no-store',
      'Content-Type': 'text/html; charset=utf-8',
    },
  });
}
