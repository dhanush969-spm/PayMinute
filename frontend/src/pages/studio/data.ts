// Studio data sources: payments, risk signals, status events, budgets

import type { AgDataSourcesDefinition, AgFieldDefinition, AgFormat, AgRelationDefinition } from "ag-studio";

export interface StudioTables {
  asOf: string;
  currency: string;
  payments: Record<string, unknown>[];
  riskSignals: Record<string, unknown>[];
  statusEvents: Record<string, unknown>[];
  budgets: Record<string, unknown>[];
}

const CALENDAR_ID = "calendar";
type FieldSpec = [id: string, name: string, format: AgFormat, hide?: boolean];
const fields = (specs: FieldSpec[]): AgFieldDefinition[] =>
  specs.map(([id, name, format, hide]) => ({ id, name, format, ...(hide && { hide }) }));

const manyToOne = (id: string, source: string, target: string): AgRelationDefinition => {
  const [sourceTable, sourceField] = source.split(".");
  const [targetTable, targetField] = target.split(".");
  return { id, source: { tableId: sourceTable, fieldId: sourceField }, target: { tableId: targetTable, fieldId: targetField }, type: "many-to-one" };
};
const onCalendar = (tableId: string, fieldId: string): AgRelationDefinition => ({
  id: `${tableId}-${fieldId}-calendar`,
  source: { tableId, fieldId },
  target: { calendarId: CALENDAR_ID },
  truncate: "day",
});

export const hasData = (t: StudioTables) => t.payments.length > 0;

export function buildStudioData(t: StudioTables): AgDataSourcesDefinition {
  const dates = t.payments.map((p) => String(p.date).slice(0, 10)).sort();
  const today = t.asOf.slice(0, 10);
  return {
    description:
      `PayMinute is an AI payment guardian on PayPal for a freelancer. Every payment (PayPal Payouts sent, PayPal Checkout ` +
      `received, PayPal invoices, synced PayPal activity) is scored for fraud/scam risk before money moves. Amounts are in ` +
      `${t.currency}. moneyIn/moneyOut only count completed or in-flight payments; riskScore is 0-1. dataSource says where a ` +
      `row came from (Live, PayPal sync or Assistant).`,
    sources: [
      {
        id: "payments",
        description: "One row per payment in PayMinute's ledger, with PayPal status, AI category and AI risk score.",
        data: t.payments,
        fields: fields([
          ["id", "Payment ID", "textFormat", true],
          ["date", "Payment Date", "dateTimeFormat"],
          ["direction", "Direction", "textFormat"],
          ["channel", "PayPal Product", "textFormat"],
          ["counterparty", "Counterparty", "textFormat"],
          ["country", "Country", "textFormat"],
          ["category", "Category", "textFormat"],
          ["categorySource", "Categorised By", "textFormat"],
          ["status", "PayPal Status", "textFormat"],
          ["statusGroup", "Status Group", "textFormat"],
          ["statusReason", "Status Reason", "textFormat"],
          ["amount", "Amount", "currencyFormat"],
          ["moneyIn", "Money In", "currencyFormat"],
          ["moneyOut", "Money Out", "currencyFormat"],
          ["net", "Net", "currencyFormat"],
          ["inFlightAmount", "In Flight", "currencyFormat"],
          ["heldAmount", "Held by PayMinute", "currencyFormat"],
          ["riskScore", "Risk Score", "percentageFormat"],
          ["riskLevel", "Risk Level", "textFormat"],
          ["flagged", "Flagged", "integerFormat"],
          ["highRisk", "High Risk", "integerFormat"],
          ["payments", "Payments", "integerFormat"],
          ["dataSource", "Data Source", "textFormat"],
        ]),
      },
      {
        id: "riskSignals",
        name: "Risk Signals",
        description: "Each reason behind a payment's risk score (rules and the Isolation Forest anomaly model). Weight is 0-1.",
        data: t.riskSignals,
        fields: fields([
          ["id", "Signal ID", "textFormat", true],
          ["paymentId", "Payment ID", "textFormat", true],
          ["signal", "Risk Signal", "textFormat"],
          ["code", "Signal Code", "textFormat", true],
          ["weight", "Signal Weight", "decimalFormat"],
          ["isMl", "From ML Model", "integerFormat"],
        ]),
      },
      {
        id: "statusEvents",
        name: "Status Events",
        description: "Every PayPal status change PayMinute recorded (API polling, webhooks, holds, owner decisions).",
        data: t.statusEvents,
        fields: fields([
          ["id", "Event ID", "textFormat", true],
          ["paymentId", "Payment ID", "textFormat", true],
          ["counterparty", "Event Counterparty", "textFormat"],
          ["channel", "Event PayPal Product", "textFormat"],
          ["amount", "Event Payment Amount", "currencyFormat"],
          ["status", "Event Status", "textFormat"],
          ["reason", "Event Reason", "textFormat"],
          ["via", "Recorded Via", "textFormat"],
          ["at", "Event Time", "dateTimeFormat"],
        ]),
      },
      {
        id: "budgets",
        description: "Monthly budgets per expense category with spend so far and PayMinute's month-end forecast.",
        data: t.budgets,
        fields: fields([
          ["category", "Budget Category", "textFormat"],
          ["limit", "Budget", "currencyFormat"],
          ["spent", "Spent This Month", "currencyFormat"],
          ["forecast", "Forecast Month End", "currencyFormat"],
          ["state", "Budget State", "textFormat"],
        ]),
      },
    ],
    relationships: [
      manyToOne("signal-payment", "riskSignals.paymentId", "payments.id"),
      manyToOne("event-payment", "statusEvents.paymentId", "payments.id"),
      onCalendar("payments", "date"),
    ],
    calendars: [
      {
        id: CALENDAR_ID,
        label: "Calendar",
        range: { from: { type: "date", value: dates[0] ?? today }, to: { type: "date", value: dates.at(-1) ?? today } },
        fragments: ["year", "quarter", "month", "week", "day", "dayOfWeek"],
      },
    ],
    expressions: [
      {
        id: "flagRate",
        name: "Flag Rate",
        description: "Share of payments PayMinute flagged as medium or high risk.",
        format: "percentageFormat",
        isMeasure: true,
        expression: { operator: "divide", inputs: [{ id: "payments.flagged", aggregation: "sum" }, { id: "payments.payments", aggregation: "sum" }] },
      },
      {
        id: "budgetUse",
        name: "Budget Used",
        description: "Spent so far / budget, across the selected categories.",
        format: "percentageFormat",
        isMeasure: true,
        expression: { operator: "divide", inputs: [{ id: "budgets.spent", aggregation: "sum" }, { id: "budgets.limit", aggregation: "sum" }] },
      },
    ],
  };
}
