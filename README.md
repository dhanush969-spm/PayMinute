# PayMinute: an AI payment guardian built on PayPal

> Every PayPal payment checked before the money moves.

PayMinute is a money app for freelancers and small sellers who get paid and pay others through PayPal. You sign in with **Log in with PayPal** and pay people **by their PayPal email**: only PayPal sandbox personal accounts (`…@personal.example.com`) can be paid, so a typo'd or look-alike address never gets money. Before any money moves, its AI checks the payment for scams, fraud, duplicates and amounts that are unusual for this user. Once a payment is in flight, PayMinute tracks it and explains in plain language why it is still pending. An AI assistant built on the **official PayPal Agent Toolkit** can create and send invoices, send money and answer questions about your cash. It never moves money without your approval.

PayMinute also has **agents that shop and sell for you**. Describe what you're selling in one line and the Sell agent reads the features and condition and suggests a fair price from similar listings. On the Buy side you say what you want and your budget; your agent compares every listing feature by feature, bargains live with the seller's agent (both agents run on **Google Gemini**, which also looks up real market prices on the web), and opens a checkout where the payee and price are locked on the server. **Payment agents** pay a contractor the moment a job is marked done, run scheduled payments, and stay inside the limits you set.

Built for the [PayPal AI Hackathon](https://paypalaihackathon.devpost.com/). Uses the free **PayPal sandbox** (sandbox app name: *PayMinute*).

| Problem | What PayMinute does | PayPal APIs |
|---|---|---|
| Paying the wrong person, or a scammer | **Pay by PayPal email only**: payments go only to PayPal sandbox emails (`…@personal.example.com`); phone numbers and other domains (like look-alike `paypa1` addresses) are refused, and every payment gets an explainable fraud score first | Payouts (`EMAIL`) |
| Online payment fraud and scams | Explainable risk score before every send or capture: rules plus an **Isolation Forest** trained on *your* history | Payouts, Orders v2 (authorize → capture / void) |
| "Money left my account but it's still pending" | Status tracker that turns PayPal reason codes into plain language, an expected resolution window (learned from your past payments) and next steps | Payments v2 captures, Payouts items, Webhooks |
| Duplicate payments | Catches repeat sends and customers who pay twice; idempotency keys stop double charges | Payouts, `PayPal-Request-Id` |
| Hard-to-manage expenses | Automatic categories (memory → rules → LLM); one correction teaches every future payment from that contact | Transaction Search |
| Financial planning | Budgets, month-end forecasts, recurring-payment detection, AI coach | All of the above |
| Doing it all by chat | An LLM agent using **PayPal Agent Toolkit** tools (invoices, orders, transactions, disputes) plus PayMinute tools, with approval cards. It can also mount the **PayPal MCP server** and **Zapier MCP** | Agent Toolkit, PayPal MCP server, Invoicing v2 |
| Buying and selling second-hand without haggling | **Buy and Sell agents powered by Google Gemini**: one-line listings; Gemini searches the web for real market prices and decides the price from the item's features and damage; live bargaining where Gemini picks each offer and writes every message. Code enforces the buyer's budget, the fair-price cap and the seller's private floor, then opens a locked 10-minute checkout | Payouts |
| Paying people on time, but safely | **Payment agents**: pay on job completion, weekly or monthly schedules, an auto-pay limit and a daily cap; anything bigger waits for approval | Payouts |
| Seeing the big picture | **Studio**: self-serve dashboards over every payment, risk signal and status change, built with **AG Studio** and its AI agent framework | All of the above |

---

## Quick start (about 10 minutes)

### 1. Prerequisites
- Python 3.11+ and Node.js 18+
- A free PayPal Developer account (in India? see [docs/INDIA.md](docs/INDIA.md): use US sandbox accounts and USD). See **[docs/SANDBOX_SETUP.md](docs/SANDBOX_SETUP.md)** to create the sandbox app and enable Payouts, Invoicing, Transaction Search and **Log in with PayPal** (Return URL `http://localhost:5173/api/auth/callback`), and to create two sandbox personal accounts.
- **Google Gemini key** (free, from https://aistudio.google.com/apikey) in `GEMINI_API_KEY`: Gemini then runs every AI agent: AI Guard chat (with PayPal's Agent Toolkit and PayPal's sandbox MCP server as its tools), market prices from the web, the Buy/Sell bargaining, phone-check and insight summaries. Without a key everything still works on PayMinute's built-in planner and rules. `GEMINI_THINKING` (none, low, medium, high) sets how much Gemini thinks before answering.

### 2. Configure
```bash
git clone <this repo's URL> payminute
cd payminute
cp .env.example .env        # then edit .env: sandbox Client ID / Secret, APP_BASE_URL, SESSION_SECRET, GEMINI_API_KEY
```

### 3. Run the backend (FastAPI)
```bash
cd backend
python -m venv .venv
# macOS/Linux: source .venv/bin/activate     Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install --no-deps paypal-agent-toolkit==1.11.0
uvicorn app.main:app --reload --port 8000
```
> Why `--no-deps`? PayMinute uses the toolkit's framework-neutral core (`paypal_agent_toolkit.shared`: tool definitions, schemas and REST handlers). That core needs only `requests` and `pydantic`, which are already installed. Leaving out the toolkit's pinned CrewAI, LangChain and OpenAI Agents extras keeps the install small and avoids version conflicts.

### 4. Run the frontend (React + Vite)
```bash
cd frontend
npm install
npm run dev          # http://localhost:5173  (proxies /api to :8000)
```
To serve everything from one port instead, run `npm run build`. FastAPI then serves `frontend/dist` at http://localhost:8000.

### 5. Try it
| Where | What to do |
|---|---|
| **Login** | Open http://localhost:5173 → **Log in with PayPal** as sandbox Personal **B**, then log out (PayMinute now knows B's PayPal email). Log in as Personal **A**. |
| **Pay** | Type B's PayPal email (`…@personal.example.com`) → AI Guard shows whose email it is → $25 → Pay. A phone number, or an email on another domain such as `support@paypa1-secure.com`, is refused. |
| **Pay** (risky) | B's email, $2,500, note "urgent gift card to verify your account" → high risk, needs explicit confirmation. |
| **Get paid** | Create a $120 checkout and pay with the PayPal button as a sandbox personal account. PayMinute authorizes, scores the buyer, then captures or holds. |
| **History** | Open any payment for its status explanation, risk reasons and timeline. **Sync from PayPal** imports Transaction Search data. |
| **AI Guard** | "Is it safe to pay <B's email> $50?", "Send $20 to <B's email> for lunch", "Invoice <email> $250 for logo design", "Which payments are stuck?" |
| **Sell** | Log in as B. Type one line, e.g. `Canon EOS M50 camera, mirrorless, 24MP, 2 lenses, like new with box, charger and cleaning kit`. Use the AI price or raise it, then **List it**. |
| **Buy** | Log in as A. Type `camera with 2 lenses, like new, with box and cleaning kit, under $600` → **Start bargaining**. Watch the agents negotiate, then pay on the locked checkout. |
| **Payment agents** | Add a job (title, B's PayPal email, amount) → **Mark complete and pay**. Set limits on the Limits tab. |
| **Insights / Studio** | Budgets, forecasts and AG Studio dashboards over your real sandbox payments. |

**Optional sponsor integrations** (all off by default; see [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md)): the PayPal MCP server (on by default, `PAYPAL_MCP=off` to disable), `ZAPIER_MCP_URL` (Zapier MCP), `AG_STUDIO_LICENSE_KEY` (AG Studio; its AI assistant uses `STUDIO_OPENAI_API_KEY` or falls back to your `LLM_API_KEY`).

**No mock or sample data.** PayMinute only talks to the real PayPal sandbox: without sandbox keys it shows a setup screen, and you must log in with PayPal to use it. Every payment, insight and chart comes from payments you actually made in the sandbox.

**Who can be paid.** PayMinute pays PayPal emails only, never phone numbers, and only PayPal sandbox personal accounts (`…@personal.example.com`, set with `PAYPAL_EMAIL_DOMAINS`). When someone logs in with PayPal, their PayPal email is linked to their PayMinute account, so payments to it show up for them and AI Guard can say whose email it is. If PayPal didn't share an email at login, they add it once on the Sell page or in the account menu.

---

## How it uses PayPal and AI

```
React (Vite) ── /api ──► FastAPI ─┬─► PayPal REST (sandbox): OAuth2, Orders v2, Payments v2, Payouts,
   │                              │        Invoicing v2, Transaction Search, Webhooks (verify-signature)
   │ PayPal JS SDK v6             ├─► PayPal Agent Toolkit (python) ── tools ──► same PayPal client
   │ <paypal-button> (sandbox)    ├─► MCP bridge ──► PayPal MCP server (sandbox) · Zapier MCP · any MCP server
   │ AG Studio (Studio page) ─────┼─► /api/studio/data · /api/ai/responses (AG Studio AI proxy)
   │                              ├─► AI: risk engine (rules + Isolation Forest), status intelligence,
   │                              │        categoriser, insights/forecasts
   │                              ├─► Google Gemini: every AI agent (chat, web market prices, bargaining); hard limits in code
   │                              ├─► optional LLM (Claude / OpenAI) with tool calling
   │                              └─► SQLite ledger (AI annotations, status history, approvals)
```

- **Log in with PayPal**: OAuth authorization-code flow against `sandbox.paypal.com/connect`, then `/v1/identity/oauth2/userinfo` for name, email and payer ID. Signed HTTP-only session; all API routes require it.
- **Orders v2 with `intent: AUTHORIZE`**: the order is created server-side. After the buyer approves in the v6 SDK popup, PayMinute authorizes, scores the payer, then captures (Payments v2) or holds for review (capture or **void**).
- **Payouts**: pay a PayPal sandbox email (`recipient_type: EMAIL`, `…@personal.example.com` only). `sender_item_id` doubles as the idempotency key. PayMinute tracks item statuses (`UNCLAIMED`, `PENDING`, `ONHOLD`, …) and can **cancel unclaimed** items.
- **Webhooks**: `/api/webhooks/paypal` verifies every event with `verify-webhook-signature` (raw body, all five headers), deduplicates by event ID and updates the ledger. A background poller is the fallback.
- **Transaction Search**: imports account activity and categorises it.
- **PayPal Agent Toolkit**: invoice, order, transaction and dispute tools are given to the LLM. Tools that email customers (`send_invoice`, reminders, cancel) and PayMinute's `send_money` need approval.
- Follows PayPal's integration checklist: env-var credentials, cached OAuth token, `PayPal-Request-Id` on every POST, retry with backoff on 429/5xx, `debug_id` logged, sandbox URLs only, server-side order creation, webhook signature verification.

Details: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

## AI tools used (for the Devpost "Tools" field)
| Tool | How it's used |
|---|---|
| **PayPal Agent Toolkit** (`paypal-agent-toolkit` 1.11) | Tool definitions, JSON schemas and REST handlers for invoices, orders, transactions and disputes. They are exposed to the LLM and run through PayMinute's PayPal client. |
| **AI Guard planner + PayPal MCP server** | AI Guard reads each request, scores risk and calls PayPal tools from PayPal's sandbox MCP server (`mcp.sandbox.paypal.com`, as in PayPal's AI Toolkit) and the local Agent Toolkit. Money-moving tools wait for approval. Optional Claude or OpenAI (`LLM_PROVIDER`) adds free-form chat. |
| **scikit-learn Isolation Forest** | Per-user anomaly detection on amount, time of day, velocity and new-counterparty features, retrained on every check from the user's own history. |
| **Rule-based explainable scoring** | Scam-phrase detection, look-alike or disposable domains, velocity, new country, duplicates. Combined with noisy-OR so every reason is visible. |
| **Google Gemini** (`GEMINI_API_KEY`, default model `gemini-flash-latest`, backup `gemini-2.5-flash`) | Runs every AI agent. **Prices:** Gemini uses Google Search grounding to find what the item sells for right now (sold listings, marketplaces, retail) and decides a fair price from its features, condition and damage; the sources are shown in the app. **Bargaining:** for every turn it picks the buyer or seller agent's move and writes the message in its own words, with a short reason shown for your own agent. **AI Guard:** answers chat and calls PayPal tools. Code keeps the money rules: the buyer never goes over budget or ~10% above the fair price, the seller never goes below its private floor or reveals it, listing text is passed only as data. Rate limits (HTTP 429) are retried, then the backup model is tried, then the built-in rules take over; each one is shown in the app, listed at `/api/ai/events` and written to `payminute-ai.log`. |
| **Market agents** (PayMinute) | Feature and condition extraction from one line, and the AI fair price from comparable listings. |
| **Local intent model** (scikit-learn TF-IDF + logistic regression) | Lets AI Guard understand free-form requests ("my money hasn't shown up", "split $90 dinner with Priya") without an LLM key. |
| **Canva** | Brand and logo exploration ([design](https://canva.link/ifsizr62xqqfzbb)). |
| **PayPal MCP server** (on by default) | The Assistant connects as an MCP client to PayPal's hosted sandbox MCP server and uses its PayPal tools. |
| **AG Studio** (AG Grid) + Studio Agent Framework | The Studio page: themed self-serve dashboards over the ledger. Its AI agent explains pages and builds widgets. A server-side proxy speaks the OpenAI Responses API and runs when `STUDIO_OPENAI_API_KEY` (or an optional main LLM) is set. |
| **Zapier MCP** (optional) | Extra Assistant actions in 9,000+ apps (email, Slack…), approval-gated. |
| **PayPal AI Toolkit plugin**, **APIMatic Context Plugin**, **Postman** (development) | In Claude Code (`/plugin install paypal@claude-plugins-official`, then `/paypal:setup`): PayPal best-practice skills, SDK/API context and API testing while building. |

With a Gemini key, Gemini runs every AI agent. Without one, every safety feature and AI Guard's planner still run on built-in rules, using PayPal's MCP server and Agent Toolkit for PayPal actions.

## Project structure
```
backend/app/
  main.py            FastAPI app, background status monitor, serves the built frontend
  api.py             REST endpoints
  auth.py            Log in with PayPal (OAuth code flow, userinfo, session, PayPal email directory)
  ledger.py          payment flows: send, checkout (authorize→risk→capture/hold), sync, webhooks
  paypal/client.py   sandbox REST client (token cache, idempotency, retries, debug_id)
  paypal/service.py  Orders/Payments/Payouts/Reporting/Webhooks + PayPal Agent Toolkit wiring
  ai/risk.py         fraud/scam scoring (rules + Isolation Forest)
  ai/duplicates.py   duplicate detection
  ai/status.py       pending-payment intelligence
  ai/categorize.py   memory → rules → LLM categorisation
  ai/insights.py     budgets, forecasts, recurring detection, AI coach
  ai/llm.py          LLM client (Gemini, Claude, OpenAI), Google Search grounding, rate-limit log
  ai/agent.py        the Assistant agent + approval workflow + offline planner
  ai/negotiator.py   Google Gemini for the Buy/Sell agents: web market prices, listing reading, bargaining (hard money limits)
  ai/intent.py       local intent model (TF-IDF + logistic regression)
  market.py          Buy/Sell agents: listings, feature reading, AI price, comparison, locked quotes
  automation.py      payment agents (jobs, schedules, limits) and the rule-based negotiation fallback
  integrations/      mcp_bridge.py (PayPal/Zapier/any MCP server)
  studio.py          AG Studio data tables + AI proxy
frontend/src/        React app (Login, Home, Pay sheet, Get paid, History, Insights, Studio, AI Guard, Buy, Sell, Checkout, Payment agents)
docs/                sandbox setup, integrations, architecture, India notes
```

## Deploy (hosted demo for judges)
A `Dockerfile` and `render.yaml` are included. On [Render](https://render.com), click **New → Blueprint** and choose this repo, then set the secret env vars.

1. Render → **New → Blueprint** → pick this repo.
2. Set `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` and `APP_BASE_URL` (your Render URL).
3. In the PayPal app's **Log in with PayPal** settings, add `https://<your-render-url>/api/auth/callback` as a Return URL.
4. Optional: add a webhook to `https://<your-render-url>/api/webhooks/paypal` and set `PAYPAL_WEBHOOK_ID`.

## Responsible use
PayMinute is a hackathon project that runs in the PayPal **sandbox** only. Risk scores and time estimates are guidance, not guarantees. PayMinute gives no investment, tax or legal advice.

## License
[MIT](LICENSE)
