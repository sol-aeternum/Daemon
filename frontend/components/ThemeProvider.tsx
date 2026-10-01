'use client';

import { ThemeProvider as NextThemesProvider } from 'next-themes';

export function ThemeProvider({
  children,
  nonce,
}: {
  children: React.ReactNode;
  nonce?: string;
}) {
  return (
    <NextThemesProvider
      nonce={nonce}
      attribute="data-theme"
      defaultTheme="dark"
      storageKey="daemon-theme"
      enableSystem={true}
    >
      {children}
    </NextThemesProvider>
  );
}
