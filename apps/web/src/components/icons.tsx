/* ---------------------------------------------------------------
   Icons: a small inline-SVG set (24px grid, 1.8px stroke).
   They draw with `currentColor`, so they take the colour of the
   text around them and work in both light and dark themes.
---------------------------------------------------------------- */
import type { ReactNode } from "react";

export type IconName =
  | "mail"
  | "mail-open"
  | "shield"
  | "shield-x"
  | "sparkle"
  | "check"
  | "x"
  | "alert"
  | "user"
  | "bot"
  | "route"
  | "cog"
  | "hospital"
  | "truck"
  | "clock"
  | "pill"
  | "calendar"
  | "box"
  | "link"
  | "book";

// Path data for each icon. Kept as plain SVG children so adding one is a single line.
const PATHS: Record<IconName, ReactNode> = {
  mail: (
    <>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="m3.5 6.5 8.5 6 8.5-6" />
    </>
  ),
  "mail-open": (
    <>
      <path d="M3 10.5 12 4l9 6.5V19a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z" />
      <path d="m3.5 11 8.5 5.5 8.5-5.5" />
    </>
  ),
  shield: <path d="M12 3 5 6v5.5c0 4.3 2.9 7.7 7 9.5 4.1-1.8 7-5.2 7-9.5V6z" />,
  "shield-x": (
    <>
      <path d="M12 3 5 6v5.5c0 4.3 2.9 7.7 7 9.5 4.1-1.8 7-5.2 7-9.5V6z" />
      <path d="m9.5 9.5 5 5m0-5-5 5" />
    </>
  ),
  sparkle: <path d="M12 3.5c.6 4 1.9 5.8 5.5 6.5-3.6.7-4.9 2.5-5.5 6.5-.6-4-1.9-5.8-5.5-6.5 3.6-.7 4.9-2.5 5.5-6.5zM18.5 15c.3 1.6.8 2.2 2 2.5-1.2.3-1.7.9-2 2.5-.3-1.6-.8-2.2-2-2.5 1.2-.3 1.7-.9 2-2.5z" />,
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  x: <path d="m6.5 6.5 11 11m0-11-11 11" />,
  alert: (
    <>
      <path d="M10.3 4.2 2.8 17.5A2 2 0 0 0 4.5 20.5h15a2 2 0 0 0 1.7-3L13.7 4.2a2 2 0 0 0-3.4 0z" />
      <path d="M12 9.5v4.5M12 17.2v.1" />
    </>
  ),
  user: (
    <>
      <circle cx="12" cy="8.5" r="3.5" />
      <path d="M5 20c.8-3.6 3.5-5.5 7-5.5s6.2 1.9 7 5.5" />
    </>
  ),
  bot: (
    <>
      <rect x="4.5" y="8" width="15" height="11" rx="3" />
      <path d="M12 4.5V8M9 13v1.5M15 13v1.5" />
      <circle cx="12" cy="4" r="0.6" />
    </>
  ),
  route: (
    <>
      <circle cx="6" cy="18" r="2.2" />
      <circle cx="18" cy="6" r="2.2" />
      <path d="M8.2 18H15a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h6.8" />
    </>
  ),
  cog: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8" />
    </>
  ),
  hospital: (
    <>
      <path d="M4 20V8l8-4 8 4v12z" />
      <path d="M12 9v6M9 12h6M4 20h16" />
    </>
  ),
  truck: (
    <>
      <path d="M3 6h11v10H3zM14 9.5h4l3 3.5v3h-7" />
      <circle cx="7" cy="17.5" r="1.8" />
      <circle cx="17.5" cy="17.5" r="1.8" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </>
  ),
  pill: (
    <>
      <rect x="3.5" y="8.5" width="17" height="7" rx="3.5" transform="rotate(-45 12 12)" />
      <path d="m9.5 9.5 5 5" />
    </>
  ),
  calendar: (
    <>
      <rect x="4" y="5.5" width="16" height="14.5" rx="2" />
      <path d="M4 10h16M8.5 3.5v4M15.5 3.5v4" />
    </>
  ),
  box: (
    <>
      <path d="m12 3 8 4.5v9L12 21l-8-4.5v-9z" />
      <path d="m4 7.5 8 4.5 8-4.5M12 12v9" />
    </>
  ),
  link: <path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" />,
  book: (
    <>
      <path d="M5 4.5h10a3 3 0 0 1 3 3V20H8a3 3 0 0 1-3-3z" />
      <path d="M5 17a3 3 0 0 1 3-3h10" />
    </>
  ),
};

/** One icon. Decorative by default (hidden from screen readers); pass `label` when it carries meaning on its own. */
export function Icon({ name, size = 16, label, className = "" }: { name: IconName; size?: number; label?: string; className?: string }) {
  return (
    <svg
      className={`icon ${className}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      {PATHS[name]}
    </svg>
  );
}
