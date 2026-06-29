import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Platform Brain",
  description: "Live 3D neural visualization of the trading platform",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  // suppressHydrationWarning: browser extensions inject attributes onto
  // <html>/<body> before React hydrates (harmless), which otherwise warns.
  return (
    <html lang="en" suppressHydrationWarning>
      <body suppressHydrationWarning>{children}</body>
    </html>
  );
}
