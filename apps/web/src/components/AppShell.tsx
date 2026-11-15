import type { JSX, ReactNode } from "react";

interface AppShellProps {
  /** Rendered first: in an RTL grid that is the right-hand column. */
  sidebar: ReactNode;
  children: ReactNode;
}

/**
 * The page frame: a fixed sidebar column plus a full-height content column.
 *
 * WHY it exists: the content column is a flex column with its own height, so a
 * thread can scroll while the composer stays pinned. Doing that in each screen
 * would mean re-deriving the same layout in two places.
 */
export function AppShell({ sidebar, children }: AppShellProps): JSX.Element {
  return (
    <div className="app-shell" dir="rtl" lang="fa">
      {sidebar}
      <div className="app-shell__main">{children}</div>
    </div>
  );
}
