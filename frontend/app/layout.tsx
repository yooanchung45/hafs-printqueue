import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { Suspense } from "react";

import { AppShell } from "@/components/app-shell";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

const SITE_URL = "https://printer.hafs.hs.kr";
const SITE_NAME = "HAFS PrintQueue";
const SITE_DESCRIPTION = "외대부고 3D 프린터 출력 시스템";

export const metadata: Metadata = {
  // Resolves relative OG/icon URLs to absolute — without this, link crawlers
  // (KakaoTalk, Slack, …) receive localhost image URLs and show no preview.
  metadataBase: new URL(SITE_URL),
  title: { default: SITE_NAME, template: `%s · ${SITE_NAME}` },
  description: SITE_DESCRIPTION,
  icons: {
    icon: [
      { url: "/brand-mark.svg", type: "image/svg+xml" },
      { url: "/favicon.ico" },
      { url: "/favicon-16x16.png", sizes: "16x16", type: "image/png" },
      { url: "/favicon-32x32.png", sizes: "32x32", type: "image/png" },
    ],
    shortcut: "/brand-mark.svg",
    apple: "/favicon-180x180.png",
  },
  // KakaoTalk / Slack / iMessage link previews. Image must be PNG/JPG (SVG is
  // not supported) — brand-logo.png is 2000×480; the dimensions let clients
  // lay it out as a wide wordmark instead of hard-cropping it square.
  openGraph: {
    type: "website",
    siteName: SITE_NAME,
    title: SITE_NAME,
    description: SITE_DESCRIPTION,
    url: SITE_URL,
    locale: "ko_KR",
    images: [{ url: "/brand-logo.png", width: 2000, height: 480, alt: SITE_NAME }],
  },
  twitter: {
    card: "summary",
    title: SITE_NAME,
    description: SITE_DESCRIPTION,
    images: ["/brand-logo.png"],
  },
};

// Sets data-theme before first paint, so the page never flashes the wrong
// theme. Must run as a plain synchronous script (not a React effect, which
// would only run after the initial paint) — this is why it's injected as raw
// HTML instead of state. suppressHydrationWarning on <html> tells React not
// to complain that this script changes the attribute after the server render.
const THEME_BOOT_SCRIPT = `
(function () {
  var stored = localStorage.getItem('theme');
  var theme = stored || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  document.documentElement.setAttribute('data-theme', theme);
})();
`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ko" className={`${geistSans.variable} ${geistMono.variable}`} suppressHydrationWarning>
      {/* suppressHydrationWarning here (not just on <html>) because browser
          extensions (translation tools, AI writing assistants, etc.) commonly
          inject attributes straight onto <body> before React hydrates —
          this is Next.js's own recommended fix for that specific case, not
          related to the theme script above. */}
      <body suppressHydrationWarning>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT_SCRIPT }} />
        <Suspense fallback={<div className="boot-screen">PrintQueue를 불러오는 중</div>}>
          <AppShell>{children}</AppShell>
        </Suspense>
      </body>
    </html>
  );
}
