import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from "react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { ArrowLeft, BarChart3, Bell, Bot, Download, History, LogOut, SendHorizontal, ShieldCheck, ShoppingBag, Store, TrendingUp, Wallet, WalletCards, Zap } from "lucide-react";
import { api, AppConfig } from "./api";
import { setCurrency } from "./format";
import { AppCtx, Logo, useToasts } from "./components/ui";
import { IdleWarning, useSession } from "./components/session";
import TransactionDrawer from "./components/TransactionDrawer";
import Avatar from "./screens/Avatar";
import SendSheet, { SendPrefill } from "./screens/SendSheet";
import { SheetCtx } from "./screens/shared";
import GHome from "./screens/Home";
import GHistory from "./screens/History";
import GInsights from "./screens/InsightsLite";
import GAiGuard from "./screens/AiGuard";
import { LoginScreen, MyPayPalEmail, SetupScreen } from "./screens/Login";
import GetPaid from "./pages/GetPaid";
import Insights from "./pages/Insights";
import Agents from "./pages/Agents";
import Buy from "./pages/Buy";
import Sell from "./pages/Sell";
import Checkout from "./pages/Checkout";
import Account from "./pages/Account";

// Studio is large, load it on demand
const Studio = lazy(() => import("./pages/Studio"));

const TABS = [
  { to: "/", label: "Home", icon: WalletCards, end: true },
  { to: "/activity", label: "History", icon: History },
  { to: "/insights", label: "Insights", icon: TrendingUp },
  { to: "/assistant", label: "AI Guard", icon: Bot },
];
const NAV = [
  { group: "Money", items: [
    { to: "/", label: "Home", icon: WalletCards, end: true },
    { to: "/activity", label: "History", icon: History },
    { to: "/insights", label: "Insights", icon: TrendingUp, match: "/insights" },
    { to: "/get-paid", label: "Get paid", icon: Download },
    { to: "/account", label: "PayPal account", icon: Wallet },
  ] },
  { group: "Agents", items: [
    { to: "/assistant", label: "AI Guard", icon: Bot },
    { to: "/buy", label: "Buy with an agent", icon: ShoppingBag, match: "/checkout" },
    { to: "/sell", label: "Sell with an agent", icon: Store },
    { to: "/agents", label: "Payment agents", icon: Zap },
  ] },
  { group: "Tools", items: [
    { to: "/studio", label: "Studio dashboards", icon: BarChart3 },
  ] },
] as { group: string; items: { to: string; label: string; icon: typeof Bot; end?: boolean; match?: string }[] }[];
// sub pages: phones show a back arrow and this title
const TITLES: Record<string, string> = {
  "/get-paid": "Get paid", "/insights/full": "Insights", "/agents": "Payment agents",
  "/buy": "Buy with an agent", "/sell": "Sell with an agent", "/studio": "Studio", "/account": "PayPal account",
};
const PAGE_TITLES: Record<string, string> = { ...TITLES, "/": "Home", "/activity": "History", "/insights": "Insights", "/assistant": "AI Guard" };

export default function App() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const [send, setSend] = useState<{ open: boolean; prefill: SendPrefill }>({ open: false, prefill: {} });
  const [menu, setMenu] = useState(false);
  const { toast, view: toasts } = useToasts();
  const { pathname } = useLocation();

  const [bootError, setBootError] = useState<string | null>(null);
  const reloadConfig = useCallback(() => {
    api.get<AppConfig>("/config").then((c) => { setCurrency(c.currency, c.locale); setConfig(c); setBootError(null); })
      .catch((e) => setBootError((e as Error).message));
  }, []);
  useEffect(reloadConfig, [reloadConfig]);
  // why the last session ended, shown on the login screen
  const [notice, setNotice] = useState<string | null>(null);
  const sessionEnded = useCallback((why: string) => { setNotice(why); setMenu(false); reloadConfig(); }, [reloadConfig]);
  // a 401 means the server ended the session (idle, expired, logged out elsewhere): show the login screen
  useEffect(() => {
    const onOut = (e: Event) => sessionEnded((e as CustomEvent<string>).detail || "Your session ended. Log in with PayPal again.");
    window.addEventListener("payminute:logged-out", onOut);
    return () => window.removeEventListener("payminute:logged-out", onOut);
  }, [sessionEnded]);
  const session = useSession({
    enabled: !!config?.auth_required && !!config?.user,
    idleMinutes: config?.session?.idle_minutes ?? 15,
    expiresAt: config?.session?.expires_at,
    onEnded: sessionEnded,
  });
  useEffect(() => { if (config?.user) setNotice(null); }, [config?.user]);

  async function logout(everywhere = false) {
    setMenu(false);
    if (everywhere && !window.confirm("Log out of PayMinute on every device and browser?")) return;
    await session.logout(everywhere);
  }

  const ctx = useMemo(() => ({
    config, toast, version, reloadConfig,
    openTx: (id: string) => setOpenId(id),
    bump: () => setVersion((v) => v + 1),
  }), [config, toast, version, reloadConfig]);
  const sheets = useMemo(() => ({
    openSend: (prefill: SendPrefill = {}) => setSend({ open: true, prefill }),
  }), []);

  useEffect(() => setMenu(false), [pathname]);
  const wide = pathname.startsWith("/studio");
  const subTitle = TITLES[pathname] ?? (pathname.startsWith("/checkout/") ? "Checkout" : undefined);
  const pageTitle = PAGE_TITLES[pathname] ?? subTitle ?? "PayMinute";
  const user = config?.user;
  const userName = user?.name ?? config?.business_name ?? "PayMinute";

  if (!config) {
    return (
      <div className="min-h-screen bg-[#f1f3f4] flex flex-col items-center justify-center gap-3 p-6 text-center text-sm text-slate-600 font-jakarta">
        {bootError ? (
          <>
            <div className="text-black">PayMinute can't start</div>
            <div className="max-w-sm">{bootError}</div>
            <button className="gbtn px-4 py-2 rounded-full bg-[#1a73e8] text-white" onClick={reloadConfig}>Try again</button>
          </>
        ) : "Loading PayMinute…"}
        {toasts}
      </div>
    );
  }
  if (!config.configured) return <SetupScreen config={config} />;
  if (config.auth_required && !user) return <><LoginScreen notice={notice} />{toasts}</>;

  const accountMenu = (side: "left-0" | "right-0") => menu && (
    <div className={`absolute top-12 ${side} z-40 w-72 bg-white rounded-2xl border border-slate-200 shadow-lg p-3 space-y-2`}>
      <div className="text-sm text-black truncate">{userName}</div>
      {user?.email && <div className="text-xs text-slate-500 truncate">{user.email}</div>}
      <div className="text-[11px] text-slate-500">Signed in with PayPal (sandbox){user?.payer_id ? ` · ${user.payer_id}` : ""}</div>
      {user && <MyPayPalEmail user={user} onSaved={reloadConfig} toast={toast} />}
      <Link to="/account" className="flex items-center gap-2 px-3 py-2 rounded-xl hover:bg-slate-50 text-sm text-black no-underline"><Wallet className="w-4 h-4" /> PayPal account &amp; balance</Link>
      {config.auth_required && (
        <>
          <button className="gbtn w-full flex items-center gap-2 px-3 py-2 rounded-xl hover:bg-slate-50 text-sm text-black" onClick={() => logout()}>
            <LogOut className="w-4 h-4" /> Log out
          </button>
          <button className="gbtn w-full flex items-center gap-2 px-3 py-2 rounded-xl hover:bg-slate-50 text-sm text-red-700" onClick={() => logout(true)}>
            <LogOut className="w-4 h-4" /> Log out on all devices
          </button>
          <div className="text-[11px] text-slate-500 px-3">You're logged out automatically after {config.session?.idle_minutes ?? 15} minutes without activity.</div>
        </>
      )}
    </div>
  );
  const headerActions = (
    <div className="flex items-center gap-2">
      <span className="hidden sm:inline text-[11px] px-2.5 py-1 rounded-full border bg-blue-50 text-[#1558b0] border-blue-200">PayPal Sandbox</span>
      <Link to="/activity?f=progress" className="w-10 h-10 rounded-full bg-slate-100 hover:bg-slate-200 flex items-center justify-center text-black" title="Payments in progress" aria-label="Payments in progress">
        <Bell className="w-5 h-5" />
      </Link>
    </div>
  );

  return (
    <AppCtx.Provider value={ctx}>
      <SheetCtx.Provider value={sheets}>
        <div className="min-h-screen bg-[#f1f3f4] text-black font-jakarta phone">
          {/* desktop: sidebar navigation */}
          <aside className="hidden lg:flex fixed inset-y-0 left-0 w-64 bg-white border-r border-slate-200 flex-col z-30" aria-label="Sidebar">
            <Link to="/" className="flex items-center gap-2.5 px-5 h-16 no-underline text-black border-b border-slate-100">
              <Logo size={28} /><span className="text-lg">PayMinute</span>
            </Link>
            <div className="px-4 pt-4">
              <button className="gbtn w-full py-2.5 rounded-full bg-[#1a73e8] text-white text-sm flex items-center justify-center gap-2" onClick={() => sheets.openSend()}>
                <SendHorizontal className="w-4 h-4" /> Pay
              </button>
            </div>
            <nav className="flex-1 overflow-y-auto px-3 py-3 space-y-4" aria-label="Main">
              {NAV.map((g) => (
                <div key={g.group}>
                  <div className="px-3 pb-1 text-[11px] uppercase tracking-wide text-slate-400">{g.group}</div>
                  {g.items.map((t) => (
                    <NavLink key={t.to} to={t.to} end={t.end}
                      className={({ isActive }) => `flex items-center gap-3 px-3 py-2 rounded-full text-sm no-underline ${isActive || (t.match && pathname.startsWith(t.match)) ? "bg-blue-50 text-[#1558b0]" : "text-slate-700 hover:bg-slate-50"}`}>
                      <t.icon className="w-[18px] h-[18px]" /> {t.label}
                    </NavLink>
                  ))}
                </div>
              ))}
            </nav>
            {config.auth_required && (
              <div className="px-3 pt-3 border-t border-slate-100">
                <button className="gbtn w-full flex items-center gap-3 px-3 py-2 rounded-full text-sm text-slate-700 hover:bg-slate-50" onClick={() => logout()}>
                  <LogOut className="w-[18px] h-[18px]" /> Log out
                </button>
              </div>
            )}
            <div className="p-3 text-[11px] text-slate-500 flex items-center gap-1.5">
              <ShieldCheck className="w-3.5 h-3.5 text-emerald-600" /> AI Guard checks every payment
            </div>
          </aside>

          <div className="lg:pl-64 min-h-screen flex flex-col">
            <header className="px-4 lg:px-8 h-16 bg-white sticky top-0 z-20 border-b border-slate-200 flex items-center justify-between gap-3">
              {/* phones: back arrow on sub pages, account on main tabs; desktop: page title */}
              <div className="flex items-center gap-3 min-w-0">
                {subTitle && (
                  <Link to="/" className="lg:hidden w-9 h-9 rounded-full flex items-center justify-center text-black hover:bg-slate-100" aria-label="Back"><ArrowLeft className="w-5 h-5" /></Link>
                )}
                {subTitle ? <span className="lg:hidden text-base text-black truncate">{subTitle}</span> : (
                  <div className="lg:hidden flex items-center gap-3 relative">
                    <button className="gbtn rounded-full" onClick={() => setMenu(!menu)} aria-label="Account" aria-expanded={menu}>
                      <Avatar name={userName} seed={user?.email || userName} size={40} />
                    </button>
                    <div>
                      <div className="text-[11px] text-slate-500">Welcome back</div>
                      <div className="text-sm text-black flex items-center gap-1">{userName}{user?.verified && <ShieldCheck className="w-4 h-4 text-emerald-600" aria-label="Verified PayPal account" />}</div>
                    </div>
                    {accountMenu("left-0")}
                  </div>
                )}
                <h1 className="hidden lg:block m-0 text-xl text-black truncate">{pageTitle}</h1>
              </div>
              <div className="flex items-center gap-3">
                {headerActions}
                <div className="hidden lg:block relative">
                  <button className="gbtn flex items-center gap-2.5 pl-1 pr-3 py-1 rounded-full hover:bg-slate-100" onClick={() => setMenu(!menu)} aria-label="Account" aria-expanded={menu}>
                    <Avatar name={userName} seed={user?.email || userName} size={34} />
                    <span className="text-sm text-black max-w-[160px] truncate">{userName}</span>
                    {user?.verified && <ShieldCheck className="w-4 h-4 text-emerald-600" aria-label="Verified PayPal account" />}
                  </button>
                  {accountMenu("right-0")}
                </div>
              </div>
            </header>

            <main className={`flex-1 w-full mx-auto px-4 pt-4 lg:px-8 lg:pt-6 pb-28 lg:pb-10 ${wide ? "max-w-[1600px]" : "max-w-[1240px]"}`} id="main">
              <Routes>
                <Route path="/" element={<GHome />} />
                <Route path="/activity" element={<GHistory />} />
                <Route path="/insights" element={<GInsights />} />
                <Route path="/insights/full" element={<Insights />} />
                <Route path="/assistant" element={<GAiGuard />} />
                <Route path="/get-paid" element={<GetPaid />} />
                <Route path="/agents" element={<Agents />} />
                <Route path="/account" element={<Account />} />
                <Route path="/buy" element={<Buy />} />
                <Route path="/sell" element={<Sell />} />
                <Route path="/checkout/:quoteId" element={<Checkout />} />
                <Route path="/studio" element={<Suspense fallback={<div className="skeleton" style={{ height: 400 }} />}><Studio /></Suspense>} />
                <Route path="*" element={<GHome />} />
              </Routes>
            </main>
          </div>

          {/* phones and tablets: bottom tabs */}
          <nav className="lg:hidden fixed bottom-0 left-0 right-0 bg-white border-t border-slate-200 z-30 px-2 pt-2 pb-[calc(8px+env(safe-area-inset-bottom))]" aria-label="Tabs">
            <div className="max-w-xl mx-auto grid grid-cols-4 gap-1">
              {TABS.map((t) => (
                <NavLink key={t.to} to={t.to} end={t.end}
                  className={({ isActive }) => `flex flex-col items-center gap-1 py-1.5 rounded-2xl no-underline ${isActive ? "text-[#1a73e8]" : "text-slate-600"}`}>
                  {({ isActive }) => (
                    <>
                      <span className={`px-5 py-1 rounded-full ${isActive ? "bg-blue-50" : ""}`}><t.icon className="w-5 h-5" /></span>
                      <span className="text-[11px]">{t.label}</span>
                    </>
                  )}
                </NavLink>
              ))}
            </div>
          </nav>

          {session.secondsLeft !== null && <IdleWarning seconds={session.secondsLeft} onStay={session.stay} onLogout={() => logout()} />}
          <SendSheet open={send.open} prefill={send.prefill} onClose={() => setSend({ open: false, prefill: {} })} />
          <TransactionDrawer id={openId} onClose={() => setOpenId(null)} />
          {toasts}
        </div>
      </SheetCtx.Provider>
    </AppCtx.Provider>
  );
}
