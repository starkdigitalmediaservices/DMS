import type { Metadata } from "next";
import "./globals.css";
import { cookies } from "next/headers";
import { Noto_Sans_Devanagari } from "next/font/google";
import { I18nProvider } from "@/lib/i18n";
import { LOCALE_COOKIE_KEY, SUPPORTED_LOCALES } from "@/lib/locale";
import { OnlineStatusProvider } from "@/hooks/useOnlineStatus";

const notoSansDevanagari = Noto_Sans_Devanagari({
  subsets: ["devanagari", "latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-noto-devanagari",
});

export const metadata: Metadata = {
  title: "DMS Ai",
  description: "DMS Ai verification system",
  icons: {
    icon: [
      { url: "/stark-icon-32.png", type: "image/png", sizes: "32x32" },
      { url: "/stark-icon-16.png", type: "image/png", sizes: "16x16" },
      { url: "/favicon.ico", sizes: "any" },
    ],
    apple: [
      { url: "/apple-touch-icon.png", sizes: "180x180", type: "image/png" },
    ],
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // T95 — the actual locale is only known client-side (localStorage / user
  // profile), but a cookie mirror lets the very first server-rendered byte
  // carry the right lang instead of always claiming "en" until I18nProvider's
  // effect corrects it after hydration. See lib/i18n.tsx's setLocale/effect,
  // which write this same cookie on every locale change.
  const cookieLocale = cookies().get(LOCALE_COOKIE_KEY)?.value;
  const lang = (SUPPORTED_LOCALES as readonly string[]).includes(cookieLocale ?? "")
    ? (cookieLocale as (typeof SUPPORTED_LOCALES)[number])
    : "en";

  return (
    <html lang={lang} className={notoSansDevanagari.variable}>
      <body className="bg-gdriveBg min-h-screen text-gdriveTextMain overflow-hidden select-none">
        <I18nProvider>
          <OnlineStatusProvider>{children}</OnlineStatusProvider>
        </I18nProvider>
      </body>
    </html>
  );
}
