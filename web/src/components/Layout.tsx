import type { ReactNode } from "react";
import type { Route } from "../App";
import { OfflineBanner } from "./OfflineBanner";

const TABS: { route: Route; label: string }[] = [
  { route: "today", label: "Today" },
  { route: "triage", label: "Triage" },
  { route: "pipeline", label: "Pipeline" },
];

interface Props {
  route: Route;
  children: ReactNode;
}

export function Layout({ route, children }: Props) {
  return (
    <div className="mx-auto flex min-h-dvh max-w-2xl flex-col">
      <OfflineBanner />
      <header className="flex items-start justify-between px-5 pt-6">
        <div>
          <p className="text-subtle text-[11px] font-bold uppercase tracking-[0.08em]">
            Brain Cockpit
          </p>
          <h1 className="font-cal text-emphasis mt-1 text-3xl font-extrabold capitalize leading-[0.95] -tracking-[0.02em]">
            {route}
          </h1>
        </div>
        <div className="mt-1 flex gap-2">
          <a
            href="#/integrations"
            aria-label="Integrations"
            aria-current={route === "integrations" ? "page" : undefined}
            className={`hover:border-emphasis flex h-11 w-11 items-center justify-center rounded-full border ${
              route === "integrations" ? "border-emphasis text-emphasis" : "border-subtle text-subtle"
            }`}
          >
            {/* plug / integrations, drawn inline — no icon lib */}
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path
                d="M9 2v5M15 2v5M7 7h10v3a5 5 0 0 1-10 0V7ZM12 15v7"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </a>
          <a
            href="#/settings"
            aria-label="Settings"
            aria-current={route === "settings" ? "page" : undefined}
            className={`hover:border-emphasis flex h-11 w-11 items-center justify-center rounded-full border ${
              route === "settings" ? "border-emphasis text-emphasis" : "border-subtle text-subtle"
            }`}
          >
          {/* gear, drawn inline — no icon lib */}
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path
              d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"
              stroke="currentColor"
              strokeWidth="1.8"
            />
            <path
              d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1.03 1.56V21a2 2 0 1 1-4 0v-.09A1.7 1.7 0 0 0 8.98 19.4a1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.7 1.7 0 0 0 .34-1.87 1.7 1.7 0 0 0-1.56-1.03H3a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4.6 8.98a1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 1.87.34H9a1.7 1.7 0 0 0 1.03-1.56V3a2 2 0 1 1 4 0v.09c0 .68.4 1.3 1.03 1.56a1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87V9c.26.63.88 1.03 1.56 1.03H21a2 2 0 1 1 0 4h-.09c-.68 0-1.3.4-1.51.97Z"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinejoin="round"
            />
          </svg>
          </a>
        </div>
      </header>

      <main className="flex-1 px-5 pb-28 pt-6">{children}</main>

      <nav
        aria-label="Main"
        className="bg-default border-subtle fixed inset-x-0 bottom-0 border-t"
      >
        <div className="mx-auto flex max-w-2xl">
          {TABS.map((tab) => {
            const active = route === tab.route;
            return (
              <a
                key={tab.route}
                href={`#/${tab.route}`}
                aria-current={active ? "page" : undefined}
                className={`relative flex min-h-14 flex-1 items-center justify-center text-sm ${
                  active ? "text-emphasis font-bold" : "text-subtle font-semibold"
                }`}
              >
                {active && <span aria-hidden="true" className="bg-inverted absolute inset-x-6 top-0 h-0.5" />}
                {tab.label}
              </a>
            );
          })}
        </div>
      </nav>
    </div>
  );
}
