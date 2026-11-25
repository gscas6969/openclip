import type { Metadata } from "next";
import { Suspense } from "react";
import "./globals.css";
import { Navbar } from "@/components/layout/Navbar";

export const metadata: Metadata = {
  title: "OpenClip Studio — Turn videos into viral clips",
  description: "AI-powered local-first video clipping and multi-track caption editor for TikTok, Reels, and Shorts.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <body>
        <div className="relative flex min-h-screen flex-col bg-black">
          <Suspense fallback={<div className="h-[73px] border-b border-white/5 bg-black" />}>
            <Navbar />
          </Suspense>
          <main className="flex-1">
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
