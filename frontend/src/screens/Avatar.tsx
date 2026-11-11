// First letter on a colour picked from the person's phone/email, so it stays the same.
import { Phone } from "lucide-react";

const COLORS = [
  "#1a73e8", "#188038", "#d93025", "#e37400", "#9334e6", "#007b83", "#c5221f",
  "#1967d2", "#b06000", "#a142f4", "#12b5cb", "#e52592", "#5f6368", "#137333",
];

export function avatarColor(seed: string): string {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) hash = (seed.charCodeAt(i) + ((hash << 5) - hash)) | 0;
  return COLORS[Math.abs(hash) % COLORS.length];
}

export function firstLetter(name?: string | null): string {
  const clean = (name || "").replace(/@.*/, "").replace(/[^A-Za-z0-9ऀ-෿]/g, " ").trim();
  return (clean.charAt(0) || "?").toUpperCase();
}

export default function Avatar({ name, seed, size = 48 }: { name?: string | null; seed?: string | null; size?: number }) {
  const key = (seed || name || "?").toLowerCase();
  return (
    <div
      aria-hidden="true"
      className="rounded-full flex items-center justify-center text-white shrink-0 select-none"
      style={{ width: size, height: size, background: avatarColor(key), fontSize: Math.round(size * 0.42) }}
    >
      {/^\d$/.test(firstLetter(name || seed)) ? <Phone style={{ width: size * 0.42, height: size * 0.42 }} /> : firstLetter(name || seed)}
    </div>
  );
}
