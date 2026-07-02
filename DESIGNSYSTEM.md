# Design Innsæit — Design System

A portable reference for the Design Innsæit visual language, extracted from this app's live source so it
can be replicated in other products. Everything here is app-agnostic: the tokens, type system, and
component recipes stand on their own.

---

## 1. Philosophy — one brand, two souls

The system is **dual-personality**. Each mode has exactly **one functional accent**; color-blocking is
*tonal* (steps within a hue), never rainbow. Typography does the heavy lifting.

| | **Light — Braun / Dieter Rams** | **Dark — Design Innsæit electric** |
|---|---|---|
| Canvas | warm bone/paper `#EDEAE2` | deep indigo `#1F006E` |
| Accent | hot magenta-pink `#FF006C` | electric teal `#00FFCF` |
| Type | warm ink `#161310` on bone | white `#FFFFFF` on indigo |
| Spark | — | pink `#FF006C` as a rare highlight |

**Principles**
- **Typography-led** — large, tight **Bricolage Grotesque** display; quiet **Hanken Grotesk** body.
- **One accent per mode** — pink (light) / teal (dark). Use it for the single "lit" element, not everywhere.
- **Tonal color-blocking** — build hierarchy from steps of the neutral (bone→taupe→ink / indigo→tint→ink),
  with one accent drop.
- **Flush-left, ragged-right** — no centered text on primary surfaces; lean on an underlying grid.
- **Micro-details matter** — circular nav arrows, single-letter weekday initials, dot-grid calendar,
  eyebrow labels, a Braun "systems" ruler. These small moves are what make it read as designed.

---

## 2. Color tokens

Defined as CSS custom properties on `:root` (light) and `.dark` (dark) in
`packages/config/theme/tokens.css`, then exposed to Tailwind as semantic utilities
(`bg-default`, `text-emphasis`, `border-subtle`, `text-brand-default`, …).

| Token | Utility | Light | Dark |
|---|---|---|---|
| `--cal-bg` | `bg-default` | `#EDEAE2` | `#1F006E` |
| `--cal-bg-subtle` | `bg-subtle` | `#E4E0D6` | `#1A0552` |
| `--cal-bg-emphasis` | `bg-emphasis` | `#D6D1C5` | `#3A14BE` |
| `--cal-bg-muted` | `bg-cal-muted` | `#EAE6DD` | `#190550` |
| `--cal-stamp` | `bg-cal-stamp`* | `#FBF9F3` | `#280A86` |
| `--cal-bg-inverted` | `bg-inverted` | `#161310` | `#EFE6EA` |
| `--cal-text-emphasis` | `text-emphasis` | `#161310` | `#FFFFFF` |
| `--cal-text` | `text-default` | `#524E47` | `#E2D8F5` |
| `--cal-text-subtle` | `text-subtle` | `#6E6A61` | `#9B8FC9` |
| `--cal-text-muted` | `text-muted` | `#9C988D` | `#7E70B0` |
| `--cal-text-inverted` | `text-inverted` | `#EDEAE2` | `#11003B` |
| `--cal-border` | `border-default` | `#D2CCBE` | `#36207A` |
| `--cal-border-subtle` | `border-subtle` | `#DED8CC` | `#2A1466` |
| `--cal-border-emphasis` | `border-emphasis` | `#9C988D` | `#523399` |
| `--cal-border-muted` | `border-muted` | `#E4E0D6` | `#190550` |
| `--cal-brand` | `text/bg-brand-default` | `#FF006C` | `#00FFCF` |
| `--cal-brand-emphasis` | `*-brand-emphasis` | `#D60059` | `#00C9A3` |
| `--cal-brand-text` | `text-brand` | `#FFFFFF` | `#11003B` |
| `--cal-spark` | `text/bg-spark` | `#FF006C` | `#FF006C` |

\* `--cal-stamp` is a custom surface added for the elevated "ticket stub" confirmation card.

**Runtime brand defaults** (`packages/lib/constants.ts`):
```ts
export const DEFAULT_LIGHT_BRAND_COLOR: string = "#FF006C"; // Braun pink
export const DEFAULT_DARK_BRAND_COLOR: string  = "#00FFCF"; // teal electric
```

---

## 3. Type system

Wired with `next/font/google` in `apps/web/app/layout.tsx`:

```ts
import { Bricolage_Grotesque, Hanken_Grotesk } from "next/font/google";

const hanken = Hanken_Grotesk({ subsets: ["latin"], variable: "--font-sans",
  weight: ["300","400","500","600","700","800"], display: "swap" });
const bricolage = Bricolage_Grotesque({ subsets: ["latin"], variable: "--font-cal",
  weight: ["600","700","800"], display: "swap" });
```

- **Display / headings → Bricolage Grotesque** (`font-cal`). Heavy, large, tight. Never set body copy in it.
- **Body / UI → Hanken Grotesk** (`font-sans`, the default).

**Recipes**
- **Display heading:** `font-cal text-4xl font-extrabold leading-[0.95] -tracking-[0.02em] text-emphasis`
  (go to `text-5xl`/`text-7xl` + `-tracking-[0.04em]` + `leading-[0.9]` for hero numerals/dates).
- **Eyebrow label:** `text-brand-default text-[11px] font-bold uppercase tracking-[0.18em]`
- **Form / meta label:** `text-subtle text-[11px] font-bold uppercase tracking-[0.08em]`
- **Two-tone header:** a muted `font-cal` line (name) stacked over a `font-extrabold text-emphasis` line
  (title), with a pink/teal eyebrow above.

Scale: **Display** `text-5xl–7xl` · **H1** `text-4xl` · **H2** `text-2xl` · **Body** `text-sm/base` ·
**Eyebrow** `text-[11px]`. Accent color is only used on large text or accents — never small body copy
(contrast/WCAG).

---

## 4. Email branding

Emails can't use the CSS-variable tokens, so they have an isolated palette in
`packages/emails/src/components/brandColors.ts`:

```ts
export const EMAIL_BRAND = {
  mint: "#00FFCF", mintMuted: "#9fdccf", indigo: "#2C0098", ink: "#0D0035",
  text: "#EDEAFB", pageBg: "#F3F4F6", cardBg: "#FFFFFF", bodyText: "#101010",
};
export const EMAIL_BRAND_TAGLINE = "Brand Strategy & Packaging Design Studio";
```

**Header bar** (`EmailBrandHeader.tsx`) — the constant across every email:
- Indigo `#2C0098` band, `border-radius: 12px 12px 0 0`, padding `20px 28px`, left-aligned.
- Logo image (260px, `max-width:70%`).
- Tagline in mint `#00FFCF`, 12px, `letter-spacing:0.03em`.
- Eyebrow line in muted mint `#9fdccf`, 11px, **uppercase**, `letter-spacing:0.12em` — this line carries
  **each email's own subtitle** (so it varies per template while the rest of the bar stays fixed).
- Body stays on a light background with dark text for deliverability; branding lives in the header band,
  the CTA (indigo), and accents — not a fully dark body.

---

## 5. Signature component recipes

Each is a *pattern* — rebuildable from the description + classes without this codebase.

- **Dot-grid month calendar** (`packages/features/calendars/components/DatePicker.tsx`)
  Day cells are circles: selected = filled accent, today = accent ring, available = solid tonal dot,
  closed = hollow ring. Roomy `gap-2.5`. Nav arrows are **circular** icon buttons
  (`h-9 w-9 rounded-full border hover:border-brand-default hover:text-brand-default`). Weekday row is
  **single-letter** initials (`weekdayNames(locale, weekStart, "narrow")`, `text-xs font-bold uppercase
  tracking-widest`).

- **Braun "systems" timeline ruler** (`AvailableTimes.tsx` + `AvailableTimesHeader.tsx`)
  A hairline rail (`border-l-2 border-default`) with per-slot tick marks. Header reads like an instrument:
  `font-cal text-3xl font-extrabold -tracking-[0.02em]` "Wed 12" + uppercase `Feb · 12h` + a
  `Systems · N open` sub-line. On hover/select the slot **blows up** — numeral scales to a big accent
  Bricolage figure with an accent line slicing across the rail and a square marker; label swaps
  `SELECT`→`SELECTED`. In-place transform (`scale-[2]`), animated, `motion-reduce` friendly.

- **Ticket-stub confirmation** (`bookings-single-view.tsx`, `BookingQRCode.tsx`, `public/ticket-stamp.svg`,
  `public/ticket-header.svg`)
  A postage-stamp card built by using two SVGs as CSS `mask-image` (scalloped edge + notched header),
  `--cal-stamp` surface, a directional + soft **drop-shadow** for lift, big Bricolage time numerals, a
  dashed perforation, centered captions, and a **styled QR** (rounded modules in `currentColor`, brand-
  colored finder "eyes", ECC-H). Details render as a **2×2 grid** (What/When/Who/Where) with Add-to-calendar
  / Reschedule / Cancel / Share-on-WhatsApp buttons.

- **Booking-form summary chip** (`BookEventForm.tsx`)
  "Almost there" eyebrow + two-tone title + a soft accent pill (`bg-brand-default/10 border-subtle rounded-xl`)
  with a brand dot and the selected slot; field labels scoped to the uppercase eyebrow style.

- **Two-tone editorial headers** (`event-meta/Title.tsx`, `event-meta/Members.tsx`)
  Muted `font-cal` name over a `text-emphasis font-extrabold -tracking-[0.02em] leading-[0.95]` title.

- **Solid color-block rows** (bookings list, event-type list)
  Rows are calm tonal blocks (`bg-subtle rounded-xl hover:bg-emphasis`), the imminent/today row lifted with
  a stronger tone (not an accent ring on every row). Event color snapped to a curated palette.

- **Labeled theme toggle** (`packages/features/components/ThemeToggle.tsx`)
  `next-themes` sun/moon; `withLabel` renders a bigger pill reading "Light mode" / "Dark mode".

- **Status-card dashboard** (`modules/settings/integrations-automations/…`)
  Read-only "single pane of glass": each integration/automation is a bordered card with an icon tile,
  one-line description, a `success`/`gray` `Badge`, and a deep-link "Manage" button.

- **Branded email header** — see §4.

---

## 6. Utility vocabulary

The semantic classes to mirror in another Tailwind app:

| Purpose | Classes |
|---|---|
| Surfaces | `bg-default` · `bg-subtle` · `bg-emphasis` · `dark:bg-cal-muted` |
| Text | `text-emphasis` · `text-default` · `text-subtle` · `text-muted` |
| Borders | `border-subtle` · `border-default` · `border-emphasis` |
| Accent | `text-brand-default` · `bg-brand-default` · `bg-brand-default/10` · `border-brand-default` · `text-brand` (on-accent) |
| Fonts | `font-cal` (display) · `font-sans` (body) |
| Heading feel | `font-extrabold -tracking-[0.02em] leading-[0.95]` |
| Eyebrow | `text-[11px] font-bold uppercase tracking-[0.18em]` |

Common combos: card = `bg-default border-subtle border rounded-xl p-4`; today pill =
`bg-brand-default text-brand rounded-3xl px-1`; disabled = `border-subtle text-muted`.

---

## 7. Replicate in a new app (4 steps)

1. **Drop the token sets.** Copy the `:root` (light) and `.dark` (dark) `--cal-*` blocks from
   §2 into your global CSS.
2. **Map to Tailwind semantic utilities.** Expose them as `bg-default`/`text-emphasis`/`border-subtle`/
   `brand-default`, etc. (Tailwind v4 `@theme`, or `theme.extend.colors` referencing the vars).
3. **Wire the fonts.** Add the two `next/font/google` families → `--font-cal` (Bricolage) and
   `--font-sans` (Hanken); set `--font-sans` as the default body font.
4. **Set the brand defaults.** `#FF006C` for light, `#00FFCF` for dark; use it as the single accent.

Minimal starter:

```css
:root {
  --cal-bg:#EDEAE2; --cal-bg-subtle:#E4E0D6; --cal-bg-emphasis:#D6D1C5;
  --cal-text-emphasis:#161310; --cal-text:#524E47; --cal-text-subtle:#6E6A61;
  --cal-border-subtle:#DED8CC; --cal-brand:#FF006C; --cal-brand-text:#FFFFFF;
  --font-cal:"Bricolage Grotesque"; --font-sans:"Hanken Grotesk";
}
.dark {
  --cal-bg:#1F006E; --cal-bg-subtle:#1A0552; --cal-bg-emphasis:#3A14BE;
  --cal-text-emphasis:#FFFFFF; --cal-text:#E2D8F5; --cal-text-subtle:#9B8FC9;
  --cal-border-subtle:#2A1466; --cal-brand:#00FFCF; --cal-brand-text:#11003B;
}
```

Then lead with big flush-left Bricolage headings, one accent per mode, tonal blocking, and the
micro-details (circular arrows, single-letter weekdays, eyebrow labels) — that's the look.
