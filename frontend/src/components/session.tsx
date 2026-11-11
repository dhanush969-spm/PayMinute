// Login session on the page: logs out after inactivity (with a warning first), when the session ends,
// and in every open tab at once.
import { useCallback, useEffect, useRef, useState } from "react";
import { Clock, LogOut } from "lucide-react";

const ACTIVE_KEY = "payminute.active";
const LOGOUT_KEY = "payminute.logout";
const WARN_SECONDS = 60; // or a quarter of the idle time, if that's shorter
const PING_EVERY_MS = 2 * 60 * 1000;

function store(key: string, value: string) { try { localStorage.setItem(key, value); } catch { /* private mode */ } }
function read(key: string): number { try { return Number(localStorage.getItem(key) || 0); } catch { return 0; } }

export function useSession(opts: {
  enabled: boolean; idleMinutes: number; expiresAt: number | null | undefined;
  onEnded: (notice: string) => void;
}) {
  const { enabled, idleMinutes, expiresAt, onEnded } = opts;
  const lastActive = useRef(Date.now());
  const lastPing = useRef(Date.now());
  const ended = useRef(false);
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null);

  const end = useCallback(async (notice: string, everywhere = false) => {
    if (ended.current) return;
    ended.current = true;
    setSecondsLeft(null);
    await fetch(everywhere ? "/api/auth/logout-everywhere" : "/api/auth/logout", { method: "POST", credentials: "same-origin" }).catch(() => undefined);
    store(LOGOUT_KEY, String(Date.now())); // other tabs follow
    onEnded(notice);
  }, [onEnded]);

  const stay = useCallback(() => {
    lastActive.current = Date.now();
    store(ACTIVE_KEY, String(lastActive.current));
    setSecondsLeft(null);
    lastPing.current = Date.now();
    fetch("/api/auth/ping", { method: "POST", credentials: "same-origin" })
      .then((r) => { if (r.status === 401) r.json().then((d) => end(d.detail || "Your session ended.")).catch(() => end("Your session ended.")); })
      .catch(() => undefined);
  }, [end]);

  useEffect(() => {
    if (!enabled) return;
    ended.current = false;
    lastActive.current = Date.now();
    let lastWrite = 0;
    const active = () => {
      const now = Date.now();
      lastActive.current = now;
      if (now - lastWrite > 15000) { lastWrite = now; store(ACTIVE_KEY, String(now)); } // share activity with other tabs
      if (now - lastPing.current > PING_EVERY_MS) stay(); // keep the server-side session alive while the user is here
    };
    const events = ["pointerdown", "keydown", "wheel", "touchstart", "scroll"] as const;
    events.forEach((e) => window.addEventListener(e, active, { passive: true }));
    const onStorage = (e: StorageEvent) => {
      if (e.key === LOGOUT_KEY && e.newValue && !ended.current) { ended.current = true; onEnded("You logged out in another tab."); }
    };
    window.addEventListener("storage", onStorage);

    const idleMs = idleMinutes * 60 * 1000;
    const tick = window.setInterval(() => {
      const now = Date.now();
      if (expiresAt && now >= expiresAt * 1000) { end("Your session ended. Log in with PayPal again."); return; }
      if (!idleMs) return;
      const last = Math.max(lastActive.current, read(ACTIVE_KEY));
      const left = Math.round((last + idleMs - now) / 1000);
      if (left <= 0) end(`You were logged out after ${idleMinutes} minute${idleMinutes === 1 ? "" : "s"} without activity.`);
      else setSecondsLeft(left <= Math.min(WARN_SECONDS, Math.max(10, idleMs / 4000)) ? left : null);
    }, 1000);
    return () => {
      events.forEach((e) => window.removeEventListener(e, active));
      window.removeEventListener("storage", onStorage);
      window.clearInterval(tick);
    };
  }, [enabled, idleMinutes, expiresAt, end, stay, onEnded]);

  return { secondsLeft, stay, logout: (everywhere = false) => end(everywhere ? "You logged out on all devices." : "You logged out.", everywhere) };
}

export function IdleWarning({ seconds, onStay, onLogout }: { seconds: number; onStay: () => void; onLogout: () => void }) {
  return (
    <div className="fixed inset-0 z-[90] bg-black/40 flex items-center justify-center p-4" role="alertdialog" aria-modal="true" aria-label="Session timeout">
      <div className="bg-white rounded-3xl border border-slate-200 p-6 max-w-sm w-full space-y-4 text-center shadow-xl">
        <div className="w-12 h-12 rounded-full bg-amber-50 text-amber-700 flex items-center justify-center mx-auto"><Clock className="w-6 h-6" /></div>
        <div>
          <div className="text-base text-black">Still there?</div>
          <div className="text-sm text-slate-600 mt-1">For your security, PayMinute logs you out in <span className="text-black tabular-nums">0:{String(seconds).padStart(2, "0")}</span>.</div>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <button className="gbtn py-2.5 rounded-full bg-slate-100 text-black text-sm flex items-center justify-center gap-1.5" onClick={onLogout}><LogOut className="w-4 h-4" /> Log out</button>
          <button className="gbtn py-2.5 rounded-full bg-[#1a73e8] text-white text-sm" onClick={onStay} autoFocus>Stay signed in</button>
        </div>
      </div>
    </div>
  );
}
