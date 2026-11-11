"""Opens links in a KERNEL cloud browser to see where they lead."""
from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger("payminute.kernel")

PREVIEW_JS = r"""
const target = %s;
const chain = [];
page.on('response', (r) => {
  try {
    if (r.request().isNavigationRequest() && r.frame() === page.mainFrame()) chain.push({ url: r.url(), status: r.status() });
  } catch (e) {}
});
let navError = null;
try {
  await page.goto(target, { waitUntil: 'domcontentloaded', timeout: 20000 });
  await page.waitForTimeout(1500);
} catch (e) { navError = String(e).slice(0, 300); }
const info = await page.evaluate(() => ({
  title: document.title || '',
  passwordFields: document.querySelectorAll('input[type=password]').length,
  cardFields: document.querySelectorAll('input[autocomplete*=cc-], input[name*=card i], input[name*=cvv i]').length,
  forms: document.forms.length,
  text: document.body ? document.body.innerText.slice(0, 500) : '',
})).catch(() => ({ title: '', passwordFields: 0, cardFields: 0, forms: 0, text: '' }));
const shot = await page.screenshot({ type: 'jpeg', quality: 55 }).catch(() => null);
return { finalUrl: page.url(), chain, navError, ...info, screenshot: shot ? shot.toString('base64') : null };
"""


def enabled() -> bool:
    return bool(os.getenv("KERNEL_API_KEY", "").strip())


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def preview(url: str) -> dict[str, Any]:
    if not enabled():
        raise RuntimeError("KERNEL_API_KEY is not set")
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    from kernel import Kernel

    client = Kernel(api_key=os.getenv("KERNEL_API_KEY").strip())
    browser = client.browsers.create(headless=True, stealth=True, timeout_seconds=90)
    try:
        res = client.browsers.playwright.execute(browser.session_id, code=PREVIEW_JS % json.dumps(url), timeout_sec=45)
    finally:
        try:
            client.browsers.delete_by_id(browser.session_id)
        except Exception as exc:  # best effort cleanup
            log.info("KERNEL cleanup failed: %s", exc)
    if not res.success:
        raise RuntimeError(f"KERNEL preview failed: {res.error or res.stderr}")
    data = res.result if isinstance(res.result, dict) else json.loads(res.result or "{}")
    return summarize(url, data)


def summarize(url: str, data: dict[str, Any]) -> dict[str, Any]:
    """Turn a browser visit into risk signals."""
    from ..ai.qr import PAYPAL_HOSTS, analyze

    final = data.get("finalUrl") or url
    start_host, final_host = _host(url), _host(final)
    hops = [c["url"] for c in data.get("chain") or []]
    signals = []
    if final_host and final_host != start_host:
        signals.append({"code": "redirect_other_site", "label": "Lands on a different website",
                        "detail": f"The link starts at {start_host} but ends up on {final_host}.", "weight": 0.25})
    if len(hops) >= 3:
        signals.append({"code": "redirect_chain", "label": "Long redirect chain",
                        "detail": f"{len(hops)} hops before the page loaded, a common way to hide the destination.", "weight": 0.15})
    is_paypal = final_host in PAYPAL_HOSTS or final_host.endswith(".paypal.com")
    if data.get("passwordFields") and not is_paypal:
        signals.append({"code": "asks_password", "label": "Asks for a password",
                        "detail": "The page has a password field but is not on paypal.com.", "weight": 0.55})
    if data.get("cardFields") and not is_paypal:
        signals.append({"code": "asks_card", "label": "Asks for card details",
                        "detail": "The page asks for card numbers outside PayPal.", "weight": 0.4})
    final_check = analyze(final) if final != url else None
    if final_check:
        for s in final_check["signals"]:
            if s["code"] not in {"official"}:
                signals.append({**s, "label": s["label"] + " (final page)"})
    p_safe = 1.0
    for s in signals:
        p_safe *= 1 - s["weight"]
    return {
        "start_url": url,
        "final_url": final,
        "redirects": hops,
        "title": data.get("title"),
        "nav_error": data.get("navError"),
        "page_text": (data.get("text") or "")[:300],
        "screenshot": data.get("screenshot"),
        "signals": signals,
        "score": round(1 - p_safe, 3),
        "level": "high" if 1 - p_safe >= 0.5 else ("medium" if 1 - p_safe >= 0.2 else "low"),
        "engine": "KERNEL cloud browser",
    }
