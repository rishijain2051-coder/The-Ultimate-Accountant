import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { Nav } from "@/components/Nav";
import { StatusBar } from "@/components/StatusBar";

export const metadata: Metadata = {
  title: "Invoice Bridge",
  description: "Read an invoice, check what it understood, post it to Tally.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="flex min-h-screen flex-col">
          <header className="sticky top-0 z-40 border-b hairline bg-[color-mix(in_srgb,var(--bg)_85%,transparent)] backdrop-blur-md">
            <div className="mx-auto flex max-w-[1800px] items-center gap-6 px-6 py-2.5 max-sm:gap-3 max-sm:px-3">
              <Link href="/" className="wordmark shrink-0">
                {/* The Counterfoil mark, small cut (brand/): a bill and its counterfoil split through one round mark. */}
                <svg className="mark" viewBox="32 24 192 208" aria-hidden="true">
                  <path d="M60 24 H92 V90.67 A38 38 0 0 0 92 161.33 V232 H60 A28 28 0 0 1 32 204 V52 A28 28 0 0 1 60 24 Z" />
                  <path d="M120 24 H196 A28 28 0 0 1 224 52 V204 A28 28 0 0 1 196 232 H120 V161.33 A38 38 0 0 0 120 90.67 Z" />
                </svg>
                Invoice Bridge
              </Link>
              <Nav />
              <div className="ml-auto hidden shrink-0 sm:block"><StatusBar /></div>
              {/* Ultimate Accountant's other module, through its own opening: another page of
                  the same server, outside this basePath, so a plain link. */}
              <a href="/console/?opening" className="nav-link shrink-0 max-sm:ml-auto">Switch to Assurance Console</a>
            </div>
          </header>
          <main className="flex-1 px-6 pb-12 pt-6 max-sm:px-3 max-sm:pt-4">{children}</main>
          {/* target=_blank: the app window hands it to the browser (pywebview). */}
          <footer className="border-t hairline px-6 py-3 text-[12px] faint max-sm:px-3">
            <div className="mx-auto max-w-[1800px]">
              Developed by <a className="link" href="https://draveta.vercel.app" target="_blank" rel="noopener noreferrer">Draveta</a>
            </div>
          </footer>
        </div>
      </body>
    </html>
  );
}
