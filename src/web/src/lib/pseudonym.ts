// Open Pseudonym's "What the AI saw" (Settings › Atlas Shield): each message as the AI got it, beside your own words,
// and the signed receipt every request left. The server checks the receipts (lifeos.pseudonym: GET /v1/pseudonym,
// /v1/pseudonym/turns, /v1/pseudonym/turns/{id}); this file holds the words and the small helpers the page shares.
import { clock, dayLabel } from "./view";

export type PseudonymState = { enabled: false } | { enabled: true; key: { kid: string } | null };
export type StandIns = Record<string, number>;
// words: what was hidden in your own words (the marks the sheet shows); stand_ins: what the last request carried
export type TurnRow = { id: string; at: string; job: string; model: string; calls: number; words?: StandIns; stand_ins: StandIns };
export type TurnPage = { turns: TurnRow[]; more: boolean };
export type Call = { seq: number; at: string; model: string; payload: string; stand_ins: StandIns; ok: boolean; why: string | null; token: string };
export type Mark = [number, number, string];
export type Turn = { id: string; at: string; job: string; seen: string | null; real: string | null; marks: Mark[]; words?: StandIns; stand_ins: StandIns; ok: boolean; calls: Call[] };

export const SAW_LINE = "Each message as it left Atlas for the AI. Private details went as stand-ins, and every request has a signed receipt.";
// under "The AI got": why a stand-in can look like a real address or name
export const STAND_IN_NOTE = "Highlighted parts are stand-ins. Some look real so the AI can write naturally; you always see your own details.";

// "Today, 2:10 PM": the day on this device, then the time
export function when(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  const day = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  return `${dayLabel(day, now)}, ${clock(iso)}`;
}

// what each kind of stand-in hid, in words (one, many); the SDK's type names (lifeos.pseudonym.TYPE_OF)
const KIND_WORDS: Record<string, [string, string]> = {
  CARD: ["card", "cards"],
  BANK_ACCOUNT: ["bank number", "bank numbers"],
  IBAN: ["bank number", "bank numbers"],
  ROUTING: ["routing number", "routing numbers"],
  SSN: ["SSN", "SSNs"],
  API_KEY: ["key", "keys"],
  PASSWORD: ["password", "passwords"],
  OTP: ["code", "codes"],
  EMAIL: ["email address", "email addresses"],
  PHONE: ["phone number", "phone numbers"],
  ADDRESS: ["street address", "street addresses"],
  IP_ADDRESS: ["IP address", "IP addresses"],
  PERSON: ["name", "names"],
  ORG: ["company", "companies"],
};

export function kindWords(kind: string, n: number): string {
  const [one, many] = KIND_WORDS[kind] ?? ["detail", "details"];
  return `${n} ${n === 1 ? one : many}`;
}

// "1 card and 2 email addresses", or "" when nothing was hidden
export function kindsList(s: StandIns): string {
  const parts = Object.entries(s)
    .filter(([, n]) => n > 0)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, n]) => kindWords(k, n));
  if (!parts.length) return "";
  return parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

// "1 card and 2 email addresses hidden", or the calm line (`none`) when nothing was
export function hiddenLine(s: StandIns, none = "Nothing private in it"): string {
  const list = kindsList(s);
  return list ? `${list[0].toUpperCase()}${list.slice(1)} hidden` : none;
}

// what was hidden in your words, from the marks (each distinct stand-in once; the server sends the same as `words`)
export function wordsOf(seen: string | null, marks: Mark[]): StandIns {
  if (!seen) return {};
  const found = new Map<string, string>();
  for (const [a, b, kind] of marks) if (a >= 0 && b > a && b <= seen.length) found.set(`${kind}\u0000${seen.slice(a, b)}`, kind);
  const out: StandIns = {};
  for (const kind of found.values()) out[kind] = (out[kind] ?? 0) + 1;
  return out;
}

// where a turn came from, by the ledger's job (lifeos.llm.FEATURE_OF)
export function jobLabel(job: string): string {
  if (job === "chat") return "Chat";
  if (job === "agent") return "An agent";
  if (job.startsWith("agent_mode")) return "Agent mode";
  if (job.startsWith("research")) return "Research";
  if (job.startsWith("memory") || job === "embed") return "Memory";
  if (job === "chat_title" || job === "chat_summary") return "Chat upkeep";
  if (job === "draft_email") return "Email draft";
  return "Background work";
}

// what the AI got, cut into plain words and stand-ins (marked), from the server's marks (offsets and kinds only)
export function segments(text: string, marks: Mark[]): Array<{ text: string; kind: string | null }> {
  const out: Array<{ text: string; kind: string | null }> = [];
  let at = 0;
  for (const [start, end, kind] of [...marks].sort((a, b) => a[0] - b[0])) {
    if (start < at || end > text.length || end <= start) continue; // overlapping or out of range: leave it as text
    if (start > at) out.push({ text: text.slice(at, start), kind: null });
    out.push({ text: text.slice(start, end), kind });
    at = end;
  }
  if (at < text.length) out.push({ text: text.slice(at), kind: null });
  return out;
}

// a receipt that didn't check out, in plain words (the server's short reason)
export function whyWords(why: string | null): string {
  if (why === "signature") return "Its signature isn't Atlas's";
  if (why === "chain") return "It doesn't follow the request before it";
  if (why === "place") return "It's out of order, or one before it is missing";
  if (why === "changed") return "The stored copy was changed after it was signed";
  if (why === "key") return "Atlas can't check it right now";
  return "Checks out";
}

// a fingerprint shortened in the middle, so both ends stay readable
export function shortHash(h: string): string {
  const v = h.replace(/^sha-256:/, "");
  return v.length > 16 ? `${v.slice(0, 8)}…${v.slice(-6)}` : v;
}
