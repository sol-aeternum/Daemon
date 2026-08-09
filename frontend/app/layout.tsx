import type { Metadata, Viewport } from 'next';
import { headers } from 'next/headers';
import { ThemeProvider } from '@/components/ThemeProvider';
import { AuthProvider } from '@/components/AuthProvider';
import './globals.css';

export const metadata: Metadata = {
  title: 'Daemon - Multi-Agent Assistant',
  description: 'Personal multi-agent assistant with orchestration power',
  manifest: '/manifest.json',
  appleWebApp: {
    capable: true,
    statusBarStyle: 'default',
    title: 'Daemon',
  },
  icons: {
    apple: '/icons/icon-192x192.png',
    icon: '/icons/icon-192x192.png',
  },
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  maximumScale: 1,
  viewportFit: 'cover',
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  // Surface the per-request CSP nonce to client components (notably the
  // <InlineArtifact /> srcDoc builder, which injects the nonce into its
  // inline <style> and <script> tags so the iframe's CSP — inherited from
  // the embedding page — does not block the artifact shell). The proxy
  // sets `x-nonce` on the request headers; Next.js strips it from the
  // auto-forwarded headers unless we read it explicitly here.
  const nonce = (await headers()).get('x-nonce') ?? undefined;
  return (
    <html lang="en" suppressHydrationWarning>
      <head>{nonce ? <meta name="csp-nonce" content={nonce} /> : null}</head>
      <body className="antialiased">
        <ThemeProvider>
          <AuthProvider>{children}</AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
