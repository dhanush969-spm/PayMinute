// default Studio report: cash flow, risk, payments in flight
import type { AgPageState, AgReportState, AgWidgetLayoutState } from "ag-studio";

const at = (xTrack: number, yTrack: number, xSpan: number, ySpan: number): AgWidgetLayoutState => ({ xTrack, yTrack, xSpan, ySpan });
const title = (text: string) => ({ title: { enabled: true, text } });
const sum = (id: string) => ({ id, aggregation: "sum" as const });
const avg = (id: string) => ({ id, aggregation: "avg" as const });
const countd = (id: string) => ({ id, aggregation: "countd" as const });
const FONT = '"Inter Variable", Inter, system-ui, sans-serif';
const typo = (fontSize: number, fontWeight: "normal" | "bold" = "normal") => ({ fontFamily: FONT, fontSize, fontWeight, fontStyle: "normal" as const });
const kpi = (text: string, value: { id: string; aggregation?: "sum" | "avg" | "countd" }, sparklineX?: string) => ({
  type: "value" as const,
  dataMapping: { value: [value], ...(sparklineX && { sparklineX: [{ id: sparklineX }] }) },
  format: {
    title: { enabled: true, text, typography: typo(13, "bold") },
    caption: { enabled: true, text: "", typography: typo(0) },
    style: { typography: typo(24) },
  },
});

const cashflow: AgPageState = {
  id: "cashflow",
  widgets: {
    "date-range": { type: "date-filter", dataMapping: { value: [{ id: "payments.date" }] }, format: { style: { filterType: "relative" } } },
    "kpi-in": kpi("Money in", sum("payments.moneyIn"), "calendar::month"),
    "kpi-out": kpi("Money out", sum("payments.moneyOut"), "calendar::month"),
    "kpi-net": kpi("Net", sum("payments.net"), "calendar::month"),
    "kpi-inflight": kpi("Budget used", { id: "budgetUse" }),
    "in-out-month": {
      type: "column-chart-grouped",
      dataMapping: { categoryKey: [{ id: "calendar::month" }], valueKey: [sum("payments.moneyIn"), sum("payments.moneyOut")] },
      format: { ...title("Money in and out by month"), style: { theme: { common: { legend: { enabled: true } } } } },
    },
    "spend-category": {
      type: "donut-chart",
      dataMapping: { categoryKey: [{ id: "payments.category" }], valueKey: [sum("payments.moneyOut")] },
      format: { ...title("Where the money goes"), style: { custom: { pieDataLabel: { enabled: true }, totalLabel: { enabled: true } } } },
    },
    "budget-bars": {
      type: "bar-chart-grouped",
      dataMapping: { categoryKey: [{ id: "budgets.category" }], valueKey: [sum("budgets.limit"), sum("budgets.spent"), sum("budgets.forecast")] },
      format: { ...title("Budgets: limit, spent so far and month-end forecast"), style: { theme: { common: { legend: { enabled: true } } } } },
    },
    "top-counterparties": {
      type: "bar-chart-grouped",
      dataMapping: { categoryKey: [{ id: "payments.counterparty" }], valueKey: [sum("payments.moneyIn"), sum("payments.moneyOut")] },
      sort: [{ field: sum("payments.moneyOut"), direction: "desc" }],
      format: { ...title("Top counterparties"), style: { theme: { common: { legend: { enabled: true } } } } },
    },
  },
  widgetLayout: {
    "kpi-in": at(0, 0, 4, 7),
    "kpi-out": at(4, 0, 4, 7),
    "kpi-net": at(8, 0, 4, 7),
    "kpi-inflight": at(12, 0, 4, 7),
    "date-range": at(16, 0, 8, 7),
    "in-out-month": at(0, 7, 16, 17),
    "spend-category": at(16, 7, 8, 17),
    "top-counterparties": at(0, 24, 10, 22),
    "budget-bars": at(10, 24, 14, 22),
  },
};

const risk: AgPageState = {
  id: "risk",
  widgets: {
    "kpi-checked": kpi("Payments checked", sum("payments.payments")),
    "kpi-flag-rate": kpi("Flag rate", { id: "flagRate" }),
    "kpi-held": kpi("Held by PayMinute", sum("payments.heldAmount")),
    "kpi-avg-risk": kpi("Average risk", avg("payments.riskScore")),
    "risk-level": { type: "button-filter", dataMapping: { value: [{ id: "payments.riskLevel" }] }, format: { ...title("Risk level"), style: { dimensions: { height: 28, minWidth: 56 } } } },
    "signals": {
      type: "bar-chart-grouped",
      dataMapping: { categoryKey: [{ id: "riskSignals.signal" }], valueKey: [countd("riskSignals.id")] },
      sort: [{ field: countd("riskSignals.id"), direction: "desc" }],
      format: title("Why payments get flagged (risk signals)"),
    },
    "risk-month": {
      type: "column-chart-stacked",
      dataMapping: { categoryKey: [{ id: "calendar::month" }], valueKey: [sum("payments.payments")], legendKey: [{ id: "payments.riskLevel" }] },
      format: { ...title("Payments by risk level per month"), style: { theme: { common: { legend: { enabled: true } } } } },
    },
    "by-direction": {
      type: "sunburst-chart",
      dataMapping: { categoryKey: [{ id: "payments.direction" }, { id: "payments.riskLevel" }], valueKey: [sum("payments.amount")] },
      format: title("Value checked by direction and risk"),
    },
    "flagged-grid": {
      type: "grid",
      dataMapping: {
        cols: [
          { id: "payments.date" }, { id: "payments.counterparty" }, { id: "payments.direction" }, { id: "payments.channel" },
          { id: "payments.status" }, { id: "payments.riskLevel" }, avg("payments.riskScore"), sum("payments.amount"),
        ],
      },
      sort: [{ field: avg("payments.riskScore"), direction: "desc" }],
      format: title("Riskiest payments first"),
    },
  },
  widgetLayout: {
    "kpi-checked": at(0, 0, 4, 7),
    "kpi-flag-rate": at(4, 0, 4, 7),
    "kpi-held": at(8, 0, 4, 7),
    "kpi-avg-risk": at(12, 0, 4, 7),
    "risk-level": at(16, 0, 8, 7),
    signals: at(0, 7, 12, 18),
    "risk-month": at(12, 7, 12, 18),
    "by-direction": at(0, 25, 8, 20),
    "flagged-grid": at(8, 25, 16, 20),
  },
};

const flight: AgPageState = {
  id: "inflight",
  widgets: {
    "kpi-flight": kpi("Money in flight", sum("payments.inFlightAmount")),
    "kpi-held": kpi("Held for review", sum("payments.heldAmount")),
    "kpi-events": kpi("Status events", countd("statusEvents.id")),
    "status-group": {
      type: "donut-chart",
      dataMapping: { categoryKey: [{ id: "payments.statusGroup" }], valueKey: [sum("payments.amount")] },
      format: { ...title("Payments by status"), style: { custom: { pieDataLabel: { enabled: true }, totalLabel: { enabled: false } } } },
    },
    "events-via": {
      type: "bar-chart-grouped",
      dataMapping: { categoryKey: [{ id: "statusEvents.via" }], valueKey: [countd("statusEvents.id")] },
      format: title("How status changes reached PayMinute"),
    },
    "events-grid": {
      type: "grid",
      dataMapping: {
        cols: [
          { id: "statusEvents.at" }, { id: "statusEvents.counterparty" }, { id: "statusEvents.channel" }, { id: "statusEvents.status" },
          { id: "statusEvents.reason" }, { id: "statusEvents.via" }, { id: "statusEvents.amount" },
        ],
      },
      sort: [{ field: { id: "statusEvents.at" }, direction: "desc" }],
      format: title("PayPal status timeline"),
    },
  },
  widgetLayout: {
    "kpi-flight": at(0, 0, 8, 7),
    "kpi-held": at(8, 0, 8, 7),
    "kpi-events": at(16, 0, 8, 7),
    "status-group": at(0, 7, 10, 18),
    "events-via": at(10, 7, 14, 18),
    "events-grid": at(0, 25, 24, 22),
  },
};

export const PAGES = [
  { id: "cashflow", name: "Cash flow" },
  { id: "risk", name: "Risk & fraud" },
  { id: "inflight", name: "Payments in flight" },
];

export const defaultReport: AgReportState = { selectedPageId: cashflow.id, pages: [cashflow, risk, flight] };
