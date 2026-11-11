# AI tools from the hackathon Resources page and how PayMinute uses them

Source: https://paypalaihackathon.devpost.com/resources (checked Nov 9, 2026).

**The short version:** none of these resources provides the AI *model*. PayPal's AI Toolkit, Agent Toolkit and MCP server give an AI the PayPal *tools* (create invoice, list transactions…). The sponsor tools add dashboards, browsers, hosting and app connections. PayMinute's AI Guard doesn't need an LLM: its planner verifies numbers, scores risk with the per-user ML model and calls PayPal's MCP server and Agent Toolkit. An LLM (Claude or OpenAI) is optional for free-form chat.

| Resource | What it is | How PayMinute uses it | Status | How to turn it on |
|---|---|---|---|---|
| **PayPal Agent Toolkit** (Python) | PayPal's tool definitions and REST handlers for AI agents | The Assistant's PayPal tools: invoices, orders, transactions, disputes | ✅ Built in | Nothing: on by default |
| **PayPal MCP server** | PayPal's hosted MCP server (sandbox: `mcp.sandbox.paypal.com`) | PayMinute's assistant can connect to it as an MCP client and use PayPal's hosted tools instead of the local toolkit | ✅ Built in, optional | `PAYPAL_MCP=on` (sandbox mode) |
| **PayPal AI Toolkit** (GitHub plugin) | PayPal skills + sandbox MCP for *coding agents* (Claude Code, Cursor, Codex) | Use it while developing to check PayPal best practices (`/paypal:doctor`) | 🛠 Dev-time | See "Developer tools" below |
| **PayPal AI docs / Agent tools reference** | Documentation | Source of the tool list and configuration | 📖 Reference | — |
| **AG Grid: AG Studio** | Embedded analytics: dashboards, grids, charts, **Studio Agent Framework** | **Studio** page: a self-serve dashboard over PayMinute's PayPal ledger (cash flow, risk & fraud, payments in flight). Its built-in AI agent explains pages and builds widgets | ✅ Built in | Works without a key (watermark). `AG_STUDIO_LICENSE_KEY` (free 45-day trial) removes it; the AI assistant uses `STUDIO_OPENAI_API_KEY`, or your main `LLM_API_KEY` as backup |
| **Zapier MCP** | Lets AI act in 9,000+ apps | The Assistant can mount your Zapier MCP server, e.g. "email Maya a payment reminder" or "post held payments to Slack". Every action needs your approval | ✅ Built in, optional | `ZAPIER_MCP_URL` |
| **Render** | Cloud hosting + Workflows | Hosts the live demo for judges (`render.yaml` + `Dockerfile`) | ✅ Ready | See README → Deploy |
| **Postman** | API platform + PayPal's public collection | Test PayPal sandbox calls; import PayMinute's own API from `/openapi.json` | 🛠 Dev-time | See below |
| **APIMatic Context Plugins** | Gives coding agents PayPal API/SDK context | Use while developing; free APIMatic subscription for users | 🛠 Dev-time | See below |
| **Elastic** | Search, vector search, Agent Builder | Not used. Possible next step: semantic search over transactions. Any Elastic MCP endpoint can be mounted with `MCP_SERVERS` | ➖ Optional | `MCP_SERVERS` JSON |
| **Astropods** | Hosting/runtime for production agents | Not used: PayMinute's agent runs inside its own backend | ➖ Not needed | — |
| **Channel3** | Product catalogue API for shopping agents | Not used: PayMinute isn't a shopping app | ➖ Not a fit | — |
| **Bryntum** | Gantt / scheduler / calendar | Not used. Possible next step: a calendar of upcoming and pending payments | ➖ Not used | — |

> **Prize rule:** a project can win at most **one sponsor prize**, plus one Grand *or* one Honorable Mention prize. PayMinute's strongest sponsor fit is **Best Use of AG Grid** (Studio page). Render hosting also qualifies for **Best Use of Render**. On the Devpost form, tick every sponsor tool you actually used.

---

## PayPal MCP server (in the app)
```
PAYPAL_MCP=on           # requires sandbox mode (PAYPAL_CLIENT_ID / SECRET set)
# PAYPAL_MCP_URL=https://mcp.sandbox.paypal.com/sse   (default)
```
PayMinute fetches a sandbox OAuth token with your app credentials and connects over SSE with `Authorization: Bearer <token>`, the same way PayPal's AI Toolkit configures coding agents. The server's tools appear in the Assistant side panel under **MCP servers**. Any PayPal tool the server doesn't expose falls back to the local Agent Toolkit. If the server is unreachable, the Assistant keeps working with the local toolkit and shows the server as *offline*.

## AG Studio (Studio page)
1. Optional: request the free 45-day trial licence from AG Grid (see the AG Grid page on Devpost) → `AG_STUDIO_LICENSE_KEY=...`
2. AI assistant: AG Studio's agent framework speaks the OpenAI Responses API, and PayMinute's backend proxies it at `/api/ai/responses`, so no key reaches the browser.
   - With `STUDIO_OPENAI_API_KEY=sk-...` (or `LLM_PROVIDER=openai`), requests go straight to OpenAI. Choose models with `STUDIO_AI_MODELS`.
   - Without it, the **main LLM is the backup**: whatever `LLM_PROVIDER` + `LLM_API_KEY` you set (Claude, OpenAI, Groq, OpenRouter, Ollama). PayMinute translates each Responses request into a tool-calling chat for that model and streams the answer back in Responses format (`backend/app/ai/responses_bridge.py`). Nothing extra to configure.
3. Open **Studio**. View mode shows the report, Edit mode opens Compose and Data panels, and **Save** keeps your layout in this browser.

Data sources Studio sees: `payments` (with AI category and risk score), `riskSignals` (each reason behind a score), `statusEvents` (every PayPal status change, from the API, webhooks or holds) and `budgets`.


## Zapier MCP (Assistant)
1. Sign up at https://zapier.com (14-day trial), open https://mcp.zapier.com, create a server and add actions such as *Gmail: Send Email* and *Slack: Send Channel Message*.
2. Copy the server URL → `ZAPIER_MCP_URL=...`. It contains a secret, so keep it in `.env` only.
3. Ask the Assistant: "Email maya.chen@example.com a friendly reminder about invoice INV2-…". Read-only tools run directly. Anything that sends or changes something shows an approval card.

## Developer tools (use while building; they don't run inside the app)
- **PayPal AI Toolkit plugin:** in Claude Code run `/plugin install paypal@claude-plugins-official`, then `/paypal:setup`. Try `/paypal:doctor` on this repo.
- **APIMatic Context Plugin:** follow the "Context Plugin resource info" link on the APIMatic Devpost page. It gives your coding agent PayPal SDK/API context. If you use it, claim the free subscription via the form on that page.
- **Postman:** fork PayPal's public collection (https://www.postman.com/paypal) to try sandbox calls. Import PayMinute's API with *Import → Link → `http://localhost:8000/openapi.json`* (or your Render URL).
