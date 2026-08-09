import { headers } from 'next/headers';

const CSP_MONITOR = `
window.__cspViolations = [];
document.addEventListener("securitypolicyviolation", (event) => {
  window.__cspViolations.push({
    blockedURI: event.blockedURI,
    effectiveDirective: event.effectiveDirective,
    violatedDirective: event.violatedDirective,
  });
});`;

export default async function Layout({
  children,
}: {
  children: React.ReactNode;
}) {
  const nonce = (await headers()).get('x-nonce') ?? undefined;

  return (
    <html lang="en">
      <head>
        {nonce ? <meta name="csp-nonce" content={nonce} /> : null}
        <script
          nonce={nonce}
          dangerouslySetInnerHTML={{ __html: CSP_MONITOR }}
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
