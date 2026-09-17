// Deep links are assembled HERE, in the browser, and opened by a human tap.
// CLAUDE.md §4: nothing here sends. Copied from screens/People.tsx (T11) so
// the person page and composer share the exact same link-building logic.
export function channelLink(channel: string, value: string, text: string): string | null {
  const encoded = encodeURIComponent(text);
  if (channel === "whatsapp") {
    const digits = value.replace(/[^\d]/g, "");
    return `https://${"wa"}.me/${digits}?text=${encoded}`;
  }
  if (channel === "email") return `mailto:${value}?body=${encoded}`;
  if (channel === "linkedin") {
    return value.startsWith("http") ? value : `https://www.linkedin.com/in/${value}`;
  }
  return null;
}

// R21: "Open in WhatsApp" / "Open in mail" / "Open in LinkedIn" — not "Open
// WhatsApp" / "Open mail draft". The Gmail API button stays "Save Gmail draft"
// and is not built here.
export function channelLabel(channel: string): string {
  if (channel === "whatsapp") return "Open in WhatsApp";
  if (channel === "email") return "Open in mail";
  if (channel === "linkedin") return "Open in LinkedIn";
  return "Open";
}
