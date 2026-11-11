# PayPal sandbox setup (about 5 minutes)

PayMinute only uses the free **PayPal sandbox**. No real money moves.

## 1. Create the sandbox app
1. Sign in at **https://developer.paypal.com** (any PayPal account works, or create a free one).
2. Open **Apps & Credentials** and switch the toggle at the top to **Sandbox**.
3. Click **Create App**:
   - App name: `PayMinute` (the same name as the product)
   - Type: **Merchant**
   - Sandbox account: the default **business** sandbox account
4. Click **Create App**. On the app page, copy:
   - **Client ID** → `PAYPAL_CLIENT_ID` in `.env`
   - **Secret** → `PAYPAL_CLIENT_SECRET` in `.env`

> Keep the secret in `.env` only. `.env` is git-ignored. Never paste it into the frontend, a screenshot or the README.

## 2. Turn on the features PayMinute uses
On the same app page, scroll to **Features** (sometimes called *Sandbox app settings*), enable these and click **Save**:

| Feature | Used for |
|---|---|
| Accept payments (on by default) | Orders v2 checkout: authorize → capture / void |
| **Payouts** | Send money |
| **Invoicing** | Assistant creates and sends invoices (PayPal Agent Toolkit) |
| **Transaction search** | Activity → *Sync from PayPal* |

| **Log in with PayPal** | Sign-in to PayMinute, and linking phone numbers to PayPal accounts |

If Payouts or Transaction Search returns `403 NOT_AUTHORIZED`, the feature isn't enabled yet. Enable it, save, and wait a minute.

### Log in with PayPal settings
Tick **Log in with PayPal**, then open **Advanced Settings**:

| Setting | Value |
|---|---|
| **Return URL** | Local with Vite: `http://localhost:5173/api/auth/callback` · Local single server: `http://localhost:8000/api/auth/callback` · Render: `https://<your-app>.onrender.com/api/auth/callback` |
| **Information requested from customers** | Full name, Email, **Phone number** (if offered), PayPal account ID (payer ID), account verification status |
| Privacy policy URL / User agreement URL | Any page you control, e.g. your GitHub repo's README |

The Return URL must match `APP_BASE_URL` + `/api/auth/callback` **exactly** (scheme, host, port), otherwise PayPal shows an "invalid redirect URI" error. Add one Return URL per place you run PayMinute.

Then set in `.env`: `AUTH_REQUIRED=true`, `APP_BASE_URL=…`, and a long random `SESSION_SECRET`.

## 3. Sandbox test accounts
**Testing Tools → Sandbox Accounts** already has two accounts. Create one more personal account (**Create account → Personal, United States**) so you have:
- **Business** (`sb-…@business.example.com`): the app's merchant account. Payouts are sent **from** it and checkout money goes **to** it (that's how PayPal REST credentials work).
- **Personal A** (`sb-…@personal.example.com`): **you**. Log in to PayMinute with it.
- **Personal B**: the friend you'll pay by phone. Click **⋯ → View/Edit account** to see its password and its **phone number** (Profile tab).

### How "pay by phone" works (and why)
The PayPal **sandbox doesn't support Payouts to a raw phone number** (`recipient_type: PHONE` is live-only, see PayPal's Payouts API reference). PayPal also has no API that reveals who owns a number. So PayMinute verifies and pays numbers through **PayPal's identity service**:

1. When someone logs in to PayMinute with PayPal, PayPal returns their profile (name, email, PayPal account ID and, with the `phone` scope, their **phone number**). PayMinute links that number to their PayPal account ID. If PayPal doesn't share a phone, they can add their own number from the account menu (shown as *added by you*, not *verified by PayPal*).
2. When you type a number, **AI Guard** checks it: real and dialable? which country? mobile, VoIP or premium-rate? is it **verified by PayPal**, linked, or not linked yet? It adds these signals to the payment's fraud score and the LLM explains the result in one sentence.
3. The payout goes to the linked **PayPal account ID** (`recipient_type: PAYPAL_ID`), which the sandbox supports. A number that isn't linked can't be paid: PayMinute tells you to ask them to log in once, or to pay their PayPal email.

**To set it up for a demo:** log in to PayMinute once as **Personal B** (this links B's phone), log out, then log in as **Personal A** and pay B's phone number.

## 4. Webhooks (recommended)
Webhooks push status changes, for example a pending eCheck completing or an unclaimed payout being claimed. PayPal needs a public HTTPS URL:
- **Hosted on Render**: `https://<your-app>.onrender.com/api/webhooks/paypal`
- **Local**: `ngrok http 8000`, then use `https://<id>.ngrok-free.app/api/webhooks/paypal`

On your app page, click **Add Webhook**, paste the URL and select these events:
```
PAYMENT.CAPTURE.COMPLETED      PAYMENT.CAPTURE.PENDING       PAYMENT.CAPTURE.DENIED
PAYMENT.CAPTURE.REFUNDED       PAYMENT.CAPTURE.REVERSED      PAYMENT.AUTHORIZATION.VOIDED
PAYMENT.PAYOUTS-ITEM.SUCCEEDED PAYMENT.PAYOUTS-ITEM.UNCLAIMED PAYMENT.PAYOUTS-ITEM.RETURNED
PAYMENT.PAYOUTS-ITEM.FAILED    PAYMENT.PAYOUTS-ITEM.CANCELED PAYMENT.PAYOUTS-ITEM.BLOCKED
```
Copy the **Webhook ID** into `PAYPAL_WEBHOOK_ID`. PayMinute verifies every event with PayPal's `verify-webhook-signature` API and rejects anything unverified. Without webhooks, PayMinute's background monitor polls pending payments every `STATUS_POLL_SECONDS`.

## 5. Scenarios to try in the sandbox
| Scenario | How |
|---|---|
| **Pay by phone (verified by PayPal)** | Log in once as Personal B, then as A: **Pay** → B's phone number → *Verified by PayPal (B's name)* → $25 → `SUCCESS` |
| Phone not linked | Pay `+1 415 555 0199` → AI Guard: *Not linked to PayPal yet*, the Pay button stays off |
| Risky number | Pay `+1 900 555 0199` (US premium-rate) → AI Guard: *don't pay yet* |
| Normal payout by email | **Pay by PayPal email instead** → your personal sandbox email → `SUCCESS` |
| **Unclaimed payout** (money left, not received) | Send $10 to an email that is **not** a PayPal sandbox account, e.g. `new.contractor@example.com` → `UNCLAIMED`. PayMinute explains it and offers **Cancel and get money back**. |
| Scam check (email) | Send $900 to `billing@paypa1-support.com` with note "urgent, buy gift card to verify your account" → high risk, needs explicit confirmation |
| Duplicate | Send the same amount to the same person twice within 24 h → duplicate warning |
| Checkout, low risk | **Get paid** → $120 → PayPal button → log in as the personal sandbox buyer → captured |
| Checkout held | After a few normal checkouts, create a much larger one (e.g. $950). With more risk signals (new buyer, amount far above your usual sales) PayMinute holds it. Capture or Void from the result card. |
| Invoice via AI | Assistant: "Invoice <personal sandbox email> $250 for logo design" → approve → the invoice appears in the personal account's sandbox inbox |
| Transaction Search | Activity → **Sync from PayPal**. Sandbox reporting data can lag by up to about 3 hours. |

## 6. AI tools (no extra key)
- AI Guard uses PayPal's sandbox MCP server (`PAYPAL_MCP=on`, the default) with your app's own token, plus the local PayPal Agent Toolkit. Nothing else to set up.
- Optional free-form chat: `LLM_PROVIDER=anthropic` (or `openai`) and `LLM_API_KEY=…`.
- For your coding agent: run `setup-paypal-ai-toolkit.command`, then in Claude Code `/plugin install paypal@claude-plugins-official` and `/paypal:setup`.

## Optional: PayPal AI Toolkit in your coding agent
For PayPal best-practice help while developing, install PayPal's **AI Toolkit** plugin (https://github.com/paypal/AI-Toolkit) in Claude Code, Cursor or Codex. It adds PayPal skills and connects your agent to PayPal's sandbox MCP server.
