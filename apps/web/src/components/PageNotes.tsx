import type { ReactNode } from "react";
import { Icon, type IconName } from "./icons";

/**
 * InfoNotes: two to four short "how this page works" notes, each with an icon.
 * Used under the page title on Signal Review and Audit Trail instead of one
 * long lead paragraph, so the explanation is easy to scan.
 * Styles live in views/signal-audit.css and only apply inside `.sa-page`.
 */
export function InfoNotes({ items }: { items: { icon: IconName; title: string; text: ReactNode }[] }) {
  return (
    <div className="info-notes">
      {items.map((it) => (
        <div key={it.title} className="info-note">
          <span className="info-ico">
            <Icon name={it.icon} size={18} />
          </span>
          <div>
            <h4>{it.title}</h4>
            <p>{it.text}</p>
          </div>
        </div>
      ))}
    </div>
  );
}
