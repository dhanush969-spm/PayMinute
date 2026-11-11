// Studio AI assistant, through the backend proxy
import { createAiHarness, type AgAiHarnessSetup, type AgAiPromptStarter } from "ag-studio";
import { openaiAdapter } from "./openai-adapter";

const PROMPT_STARTERS: AgAiPromptStarter[] = [
  { label: "Explain this page", prompt: "Summarise what this page shows: every widget, the fields it reads, and what stands out." },
  { label: "Anomaly alert", prompt: "Add a widget that highlights the payments with the highest risk score this month and the signal that drove each one." },
  { label: "Who triggers holds?", prompt: "Which counterparties have the most high-risk or held payments, and how much money is involved?" },
  { label: "Spend vs budget", prompt: "Add a chart comparing each budget's spend so far with its month-end forecast." },
];

export function buildAi(models: string[]): AgAiHarnessSetup {
  const adapter = openaiAdapter({ endpoint: "/api/ai", model: models[0] });
  return ({ api }) =>
    createAiHarness(api, {
      adapter,
      promptStarters: PROMPT_STARTERS,
      models: models.length > 1 ? models.map((id) => ({ id, label: id })) : undefined,
    });
}
