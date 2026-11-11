import { useEffect, useMemo, useRef, useState } from "react";
import { BarChart3, Pencil, RefreshCw, RotateCcw, Save, Sparkles } from "lucide-react";
import { AgStudioAiModule, type AgReportState, type AgStudioApi, type AgStudioMode } from "ag-studio";
import { AgStudio, AgStudioProvider } from "ag-studio-react";
import { api } from "../api";
import { EmptyState, Skeleton, useApp } from "../components/ui";
import { buildStudioData, hasData, type StudioTables } from "./studio/data";
import { payminuteStudioTheme } from "./studio/theme";
import { defaultReport, PAGES } from "./studio/report";
import { buildAi } from "./studio/ai";

const STORAGE_KEY = "payminute.studio.report.v1";

function loadSaved(): AgReportState | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as AgReportState) : null;
  } catch {
    return null;
  }
}

const withPanels = (mode: AgStudioMode, s: AgReportState): AgReportState => ({
  ...s,
  panels: { ...s.panels, filters: { ...s.panels?.filters, collapsed: mode === "edit" } },
});

export default function Studio() {
  const { config, toast, version } = useApp();
  const [tables, setTables] = useState<StudioTables | null>(null);
  const [mode, setMode] = useState<AgStudioMode>("view");
  const [pageId, setPageId] = useState(defaultReport.selectedPageId);
  const [reload, setReload] = useState(0);
  const apiRef = useRef<AgStudioApi | null>(null);
  const [initialState] = useState<AgReportState>(() => withPanels("view", loadSaved() ?? defaultReport));

  useEffect(() => {
    api.get<StudioTables>("/studio/data").then(setTables).catch((e) => toast(e.message, "error"));
  }, [version, reload, toast]);

  const data = useMemo(() => (tables ? buildStudioData(tables) : null), [tables]);
  const aiModels = config?.studio?.ai_models ?? [];
  const ai = useMemo(() => (aiModels.length ? buildAi(aiModels) : undefined), [aiModels.join(",")]);
  const pages = useMemo(() => {
    const state = apiRef.current?.getState() ?? initialState;
    return state.pages.map((p, i) => ({ id: p.id, name: PAGES.find((x) => x.id === p.id)?.name ?? `Page ${i + 1}` }));
  }, [initialState, pageId, mode]);

  function selectPage(id: string) {
    const a = apiRef.current;
    if (a) a.setState({ ...a.getState(), selectedPageId: id });
    setPageId(id);
  }
  function changeMode(next: AgStudioMode) {
    const a = apiRef.current;
    if (a) a.setState(withPanels(next, a.getState()));
    setMode(next);
  }
  function save() {
    const a = apiRef.current;
    if (!a) return;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(a.getState()));
      toast("Dashboard saved in this browser");
    } catch {
      toast("Couldn't save in this browser (storage blocked)", "error");
    }
  }
  function reset() {
    try { localStorage.removeItem(STORAGE_KEY); } catch { /* ignore */ }
    apiRef.current?.setState(withPanels(mode, defaultReport));
    apiRef.current?.clearHistory();
    setPageId(defaultReport.selectedPageId);
    toast("Restored PayMinute's default dashboard");
  }

  return (
    <div className="studio-page">
      <div className="page-head" style={{ marginBottom: 14 }}>
        <div>
          <h1 className="row" style={{ gap: 10 }}><BarChart3 size={26} color="var(--brand)" /> Studio</h1>
          <p>Self-serve analytics over your PayPal payments, risk scores and statuses. Built with AG Studio{aiModels.length ? ": ask the AI to build widgets for you" : ""}.</p>
        </div>
        <div className="row wrap">
          <div className="segmented" role="tablist" aria-label="Mode">
            <button role="tab" aria-selected={mode === "view"} className={mode === "view" ? "on" : ""} onClick={() => changeMode("view")}>View</button>
            <button role="tab" aria-selected={mode === "edit"} className={mode === "edit" ? "on" : ""} onClick={() => changeMode("edit")}><Pencil size={13} style={{ verticalAlign: -2 }} /> Edit</button>
          </div>
          <button className="btn secondary sm" onClick={() => setReload((r) => r + 1)}><RefreshCw size={14} /> Refresh data</button>
          <button className="btn secondary sm" onClick={reset}><RotateCcw size={14} /> Reset</button>
          <button className="btn primary sm" onClick={save}><Save size={14} /> Save</button>
        </div>
      </div>

      <div className="row wrap between" style={{ marginBottom: 10 }}>
        <div className="segmented" role="tablist" aria-label="Dashboard pages">
          {pages.map((p) => (
            <button key={p.id} role="tab" aria-selected={pageId === p.id} className={pageId === p.id ? "on" : ""} onClick={() => selectPage(p.id)}>{p.name}</button>
          ))}
        </div>
        <span className="small muted row" style={{ gap: 6 }}>
          <Sparkles size={14} color="var(--brand)" />
          {aiModels.length ? `AI assistant on · ${aiModels[0]}` : "AI assistant off · set STUDIO_OPENAI_API_KEY to enable"}
        </span>
      </div>

      <div className="studio-frame">
        {!data || !tables ? (
          <div className="stack" style={{ padding: 20 }}><Skeleton h={90} /><Skeleton h={260} /></div>
        ) : !hasData(tables) ? (
          <EmptyState title="No payments yet" body="Send or receive a payment in the PayPal sandbox and it shows up here." />
        ) : (
          <AgStudioProvider licenseKey={config?.studio?.license_key ?? undefined} modules={ai ? [AgStudioAiModule] : undefined}>
            <AgStudio
              style={{ height: "100%", width: "100%" }}
              theme={payminuteStudioTheme}
              layout={{ widgetBorderEnabled: true }}
              data={data}
              ai={ai}
              initialState={initialState}
              mode={mode}
              onApiReady={(e) => { apiRef.current = e.api; }}
              onStateUpdated={(e) => { if (e.state.selectedPageId !== pageId) setPageId(e.state.selectedPageId); }}
            />
          </AgStudioProvider>
        )}
      </div>
    </div>
  );
}
