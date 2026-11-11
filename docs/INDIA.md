# Running PayMinute from India

**Use US sandbox accounts and USD, even though you live in India.** The sandbox is fake money, so your country doesn't matter, and judges are global.

Why:
- PayPal stopped domestic payments in India in 2021. India-based PayPal accounts can mostly only receive international payments, and INR is supported only for in-country India accounts. Checkout, Payouts and Invoicing therefore behave inconsistently for India business accounts, even in the sandbox.
- PayPal's Agent Toolkit `create_order` tool only accepts `USD`.

## 1. Make the app use a US business account
1. developer.paypal.com → **Testing Tools → Sandbox Accounts** → **Create account**: *Business*, country **United States** → Create.
2. Create two more: *Personal*, country **United States** (A = you, B = the friend you pay by phone). They have **US phone numbers**, so you log in to PayMinute with them and pay B's US number.
3. **Apps & Credentials** → **PayMinute** → check which sandbox business account it's linked to. If it's an India account, create a new app (**Create App**, name **PayMinute**, type Merchant) and pick the **US business account** you just made. Tick **Payouts**, **Invoicing**, **Transaction search** and **Log in with PayPal** (Return URL, see SANDBOX_SETUP.md) → Save.
4. Put that app's Client ID and Secret into `.env`, then restart the backend.

## 2. Keep these settings
```
PAYMINUTE_CURRENCY=USD
PAYMINUTE_LOCALE=en-IN     # Indian number and date formatting in the UI
PAYMINUTE_PHONE_REGION=US  # your sandbox accounts have US numbers; Indian numbers (+91…) still work with the country code
```
Don't set `PAYMINUTE_CURRENCY=INR`; PayPal will reject most sandbox calls.

## 3. Other India notes
- **PayPal button doesn't appear / "not eligible":** buyer eligibility is location-based. PayMinute now shows the button anyway. Log in with the **US personal** sandbox account.
- **Indian phone numbers:** AI Guard understands them (+91, carrier, mobile/landline) and flags them as "from another country" when your region is US. They can be paid only if they're linked to a PayPal account, the same as any number.
- **Times:** the app shows times in your browser's time zone (IST). The hackathon deadline is **Nov 13, 1:30 AM IST**.
- **Render region:** Singapore is the closest region to India. Any region works for judges.
