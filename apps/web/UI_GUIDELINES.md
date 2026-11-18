# UI guidelines

The rules every page in `apps/web` follows. Check a change against this list before calling it done.

## Who it is for

The users are senior people in a large organisation that runs many hospitals: supply managers, pharmacists,
procurement and auditors. The UI should look like an enterprise analytics product, not a demo or a chat app.

## Look and feel: Power BI / Azure Databricks

- Simple but strong. Use the system sans font (`var(--sans)`), with no decorative or "fancy" type.
- The blue sidebar with white text stays. Keep the existing theme and tokens in `src/styles.css`
  (`--accent`, `--ink`, `--line`, `--surface`, ...). Don't add new colours; use the tokens.
- Headings are plain, short labels such as "Order status" or "Audit Trail", never questions or chatty lines
  ("How are the hospitals doing?", "What happened to ...?"). Nothing should read as AI-generated.
- Status uses red / amber / green through `<Status tone>` (`crit` / `warn` / `good`), always with an icon and a word.
  Never use colour alone.

## Layout: overview on top, detail below

- Each dashboard runs from the overview to the specifics:
  1. a KPI strip (`.stats` / `Stat`, as on the Stock page)
  2. the main table or chart
  3. the detail for the selected row
- Use tables for detail (`.table` inside `.tablewrap`), the same as the Network dashboard's facility detail.
  Prefer columns over stacked paragraphs.
- Use cards with the `Card` component: a small `label` line above a short `title` in `card-head`.
- Use the existing classes before writing new CSS: `card`, `card-head`, `card-body`, `table`, `stats`/`stat`,
  `tiles`/`tile`, `label`, `small`, `muted`, `row`, `row-between`, `stack`, `btn`, `btn primary`, `link`.
- Keep visual clutter down. Use tabs or toggles (`.view-tabs`) to split views instead of stacking everything,
  and hide long text behind a row click or "Details".
- Every element has to earn its place. Don't add explainer boxes or diagrams that repeat what the page already
  shows (counts the filters already show, "newest first" notes, tips on how to use a control).
- New page styles are scoped to the page (e.g. `.sa-page` in `views/signal-audit.css`) so other pages don't change.
- No horizontal page scroll. Tables wrap in `.tablewrap`, and grids collapse on narrow screens.

## Who did the work

- Every piece of work shown carries `<Origin by="agent" | "rules" | "human">` (the agent / code / person mark)
  in front of it. That way it's obvious what an agent did, what code calculated, and what a person decided.
- Agents handle language and judgement, code does the calculations and checks, and people approve.
  The UI must never show code-calculated numbers as if the AI produced them.

## What is in the MVP

- Show only the pages that make the problem → solution flow (the agent pipeline) obvious:
  detect → understand → plan → decide → act and follow up.
- Name pages for what they are, in dashboard terms (Network, Shortage Alerts, Approvals, History), not as questions.
- Explain how a number is calculated where it is shown (bands, days of supply), briefly, with `ChartGuide` or a
  small note.
- A substitute drug is always labelled as a substitute.

## Before you finish a UI change

- Run `npx tsc --noEmit -p .` and `npx vitest run` in `apps/web`.
- Don't change the global theme or other pages unless asked.
- The user checks the result in the browser themselves. Don't open a browser unless they ask.
