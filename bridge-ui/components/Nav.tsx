"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "Upload" },
  { href: "/review/", label: "Review" },
  { href: "/bank/", label: "Bank" },
  { href: "/settings/", label: "Settings" },
];

/** The screens, with the one you are on marked. */
export function Nav() {
  const path = usePathname();
  const here = (href: string) => (href === "/" ? path === "/" : path.startsWith(href.replace(/\/$/, "")));
  return (
    <nav className="scroll-x flex min-w-0 items-center gap-0.5">
      {NAV.map((n) => (
        <Link key={n.href} href={n.href} aria-current={here(n.href) ? "page" : undefined} className="nav-link">
          {n.label}
        </Link>
      ))}
    </nav>
  );
}
