import { useEffect, useRef, useState } from "react";

/* PayPal JavaScript SDK v6 (sandbox): createInstance → findEligibleMethods →
   createPayPalOneTimePaymentSession → session.start(). The order itself is created and
   authorized server-side so amounts can't be tampered with in the browser. */

declare global {
  interface Window { paypal?: any }
  namespace JSX {
    interface IntrinsicElements {
      "paypal-button": React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement> & { type?: string };
    }
  }
}

let sdkPromise: Promise<void> | null = null;
function loadSdk(src: string): Promise<void> {
  if (window.paypal?.createInstance) return Promise.resolve();
  if (!sdkPromise) {
    sdkPromise = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      s.async = true;
      s.onload = () => resolve();
      s.onerror = () => { sdkPromise = null; reject(new Error("Could not load the PayPal SDK")); };
      document.head.appendChild(s);
    });
  }
  return sdkPromise;
}

interface Props {
  clientId: string;
  sdkUrl: string;
  currency: string;
  amount: string;
  createOrder: () => Promise<{ orderId: string }>;
  onApprove: (orderId: string) => void;
  onCancel?: () => void;
  onError?: (message: string) => void;
}

export default function PayPalButton({ clientId, sdkUrl, currency, amount, createOrder, onApprove, onCancel, onError }: Props) {
  const [state, setState] = useState<"loading" | "ready" | "ineligible" | "error">("loading");
  const session = useRef<any>(null);
  const cb = useRef({ createOrder, onApprove, onCancel, onError });
  cb.current = { createOrder, onApprove, onCancel, onError };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await loadSdk(sdkUrl);
        const sdk = await window.paypal.createInstance({ clientId, components: ["paypal-payments"], pageType: "checkout", locale: "en-US" });
        // eligibility is location-based and can be wrong in the sandbox, so show the button anyway
        try {
          const methods = await sdk.findEligibleMethods({ currencyCode: currency, amount });
          if (!methods.isEligible("paypal")) console.warn("PayPal reports this buyer/currency as not eligible; showing the button anyway (sandbox).");
        } catch (err) {
          console.warn("findEligibleMethods failed; showing the button anyway", err);
        }
        if (cancelled) return;
        session.current = sdk.createPayPalOneTimePaymentSession({
          onApprove: async (data: { orderId: string }) => cb.current.onApprove(data.orderId),
          onCancel: () => cb.current.onCancel?.(),
          onError: (err: any) => cb.current.onError?.(err?.message || "PayPal error"),
        });
        setState("ready");
      } catch (e: any) {
        if (!cancelled) { setState("error"); cb.current.onError?.(e?.message || "Could not start PayPal"); }
      }
    })();
    return () => { cancelled = true; };
  }, [clientId, sdkUrl, currency, amount]);

  async function start() {
    if (!session.current) return;
    try {
      // v6 wants a Promise, not the function
      await session.current.start({ presentationMode: "auto" }, cb.current.createOrder());
    } catch (e: any) {
      cb.current.onError?.(e?.message || "Payment was not started");
    }
  }

  if (state === "loading") return <div className="skeleton" style={{ height: 48, borderRadius: 999 }} aria-label="Loading PayPal" />;
  if (state === "ineligible") return <div className="small muted">PayPal isn't available for this amount or currency.</div>;
  if (state === "error") return <div className="small" style={{ color: "var(--danger)" }}>PayPal couldn't load. Check PAYPAL_CLIENT_ID and your connection.</div>;
  return <paypal-button type="pay" className="paypal-gold" onClick={start} />;
}
