# Mobile checkout and Google sign-in review

Reviewed 28 November 2026. This review used the application source and public provider documentation. It did not read `.env`, inspect private provider accounts, create a transaction, approve a mandate, or change pricing, subscription entitlements, Google credentials, or payment-provider settings.

## UPI AutoPay findings

The displayed **monthly subscription charge**, **maximum mandate authorization**, and **mandate validity dates** describe different things. A maximum of ₹15,000 is not evidence of an immediate ₹15,000 purchase, and an expiry in 2036 is not evidence of a ten-year prepaid purchase. Customers should still review the actual charge, frequency, merchant, amount cap, and validity before authorizing.

Paddle documents a ₹15,000 maximum per subscription renewal and a mandate amount calculated from the non-discounted recurring amount. It says a higher recurring amount needs a new mandate and renewal collection requires advance notification. These rules do **not** establish why this particular ₹179 checkout showed a ₹15,000 mandate maximum. Paddle's documented recurring threshold and an individual mandate amount must not be assumed to be identical. [Paddle: UPI](https://developer.paddle.com/concepts/payment-methods/upi/)

Paytm defines recurring mandates as authorization for a validity period and frequency, with customer consent required for changes. A mandate can be paused or revoked. This supports interpreting the reported start/end dates as the authorization's validity window. The exact reason for **2036**, and which provider selected that date, remains unverified; no public source reviewed documents a universal ten-year Paddle/Paytm default. [Paytm UPI terms, recurring mandates](https://paytm.com/company/upi-terms-and-conditions)

### What Ennoic controls

Source inspection found:

- `templates/pricing.html` calls `Paddle.Checkout.open` with one configured `priceId`, quantity `1`, the authenticated email, an opaque checkout-intent token, and overlay/theme/success-URL settings.
- `/api/paddle/checkout-intent` requires authentication and CSRF protection. It creates an account-bound intent; it does not create or alter a UPI mandate.
- `paddle_billing.py` selects the configured environment and price ID. It verifies signed webhooks and matches the price and checkout intent before account synchronization.
- `entitlements.py` has an INR fallback display price of ₹179/month. Paddle's localized price preview can replace this displayed amount. The private live Paddle price entity was not inspected, so its production `billing_cycle` and tax-inclusive charge still require dashboard confirmation.
- Ennoic does not send mandate start/end dates, a ₹15,000 maximum, or a ten-year duration in this checkout payload. There is no direct Paytm merchant integration in this Paddle flow.

Paddle's published checkout parameters provide supported UI/payment-method settings, but no mandate-expiry or maximum-authorization override. Adding undocumented fields is not an appropriate fix. Provider-side configuration may exist outside this API; ask Paddle before changing an existing integration. [Paddle.Checkout.open reference](https://developer.paddle.com/paddle-js/methods/paddle-checkout-open/)

| Setting | Current control / evidence |
| --- | --- |
| Ennoic price selection and quantity | Ennoic checkout payload selects the configured Paddle price and one unit. |
| Billing cadence and actual recurring amount | The Paddle price/subscription, including taxes and any discounts. Ennoic intends monthly billing; verify `billing_cycle.interval = month`, `frequency = 1` in the live dashboard. |
| Individual mandate amount and expiry | Generated within Paddle's UPI/provider flow and displayed by the UPI app. Exact account-specific mapping requires Paddle/provider confirmation. |
| UPI ecosystem restrictions | NPCI framework, participating banks, PSPs and provider implementation. They are not application price settings. |
| Approval / pause / revoke | Customer in the UPI application, subject to applicable provider rules. |

NPCI describes mandate lifecycle management, including revoke, pause, and modify, as UPI-app operations; this does not establish a required 2036 expiry. [NPCI: Enhancement of UPI Autopay, 7 October 2025](https://www.npci.org.in/uploads/UPI_OC_No_223_FY_2025_26_Enhancement_of_UPI_Autopay_88b38535cb.pdf)

### Renewal and cancellation

Paddle subscriptions renew according to their price-derived billing cycle. The mandate's expiry is separate from this cycle. [Paddle subscription entity](https://developer.paddle.com/api-reference/subscriptions/get-subscription/)

Ennoic's **Settings → Account → Manage subscription** button requests an authenticated Paddle customer-portal session for the subscription linked to the signed-in account. Paddle's portal supports cancellation. For active subscriptions, cancellation normally takes effect at the end of the current paid period; past-due or paused subscriptions can stop sooner. Paddle also supplies portal links in customer emails. Cancel before the next renewal; Paddle documents restrictions in the final 30 minutes before a billing date. [Paddle cancellation guide](https://developer.paddle.com/build/subscriptions/cancel-subscriptions/)

Paytm also lets customers stop future automatic payments through **Active Mandates → select mandate → Cancel**. Revoking a bank authorization should not be treated as confirmation that the Paddle subscription record has been canceled: use the Paddle portal as well, check its confirmation, and keep the receipt. [Paytm cancellation instructions](https://paytm.com/faqs/upi-autopay/how-stop-cancel-upi-autopay-mandate)

### Changes delivered in this review

- A pre-existing account-deletion cancellation transport bug was found: the server posted to `/subscriptions/{id}` instead of Paddle's `/subscriptions/{id}/cancel`. Only that endpoint selection was corrected. The existing customer-ownership checks, confirmation/retry safeguards, and effective-date decisions are preserved; normal portal cancellation is unchanged. A mocked HTTP regression test checks exact sandbox/production URLs and bodies for active and past-due states. No real subscription was canceled. [Paddle cancel API](https://developer.paddle.com/api-reference/subscriptions/cancel-subscription/)
- The pricing-page disclosure now explains monthly renewal, a maximum authorization label, mandate validity, and the cancellation route. It no longer hardcodes a second ₹179 quote or promises every customer's mandate has a ₹15,000 cap. The final charge remains visible in Paddle Checkout.
- Region detection now reads Paddle's documented `data.address.countryCode` response shape. It hides the UPI explanation for an explicit non-Indian address or a non-INR preview, while retaining the existing selected-INR fallback when a preview is unavailable. This only controls explanatory copy; it does not select payment methods or change prices. [Paddle price preview](https://developer.paddle.com/api-reference/pricing-preview/preview-prices/) and [Paddle.js casing](https://developer.paddle.com/paddle-js/methods/paddle-pricepreview/)
- Paddle.js now downloads with `defer`; pricing/checkout initialization waits for `DOMContentLoaded`, after deferred scripts execute. This allows page parsing and painting during the SDK download. The price, preview, authentication checks, and checkout payload stay the same. A regression check covers a late-loaded SDK; actual network latency still depends on the device and provider.

### Provider follow-up before making a stronger promise

The account owner should contact Paddle support privately with the existing transaction/subscription reference and a redacted Paytm mandate screen. Do not post UPI IDs, mandate identifiers, tokens, or customer details in this repository. Ask:

1. Why does this specific monthly Ennoic price produce a ₹15,000 maximum when the public mandate documentation describes calculation from the non-discounted recurring amount?
2. Which participant sets the 2036 date, what exact validity rule is applied, and can Paddle configure a shorter validity or lower maximum without changing billing frequency?
3. Can the customer-facing merchant descriptor identify Ennoic more clearly while retaining Paddle's required merchant-of-record identity?
4. Confirm the live price is monthly, quantity one, and the exact renewal total/taxes; confirm cancellation behavior and the merchant-supported mandate-management flow.

Do not approve another live mandate solely to investigate. Use an existing record, a supported sandbox flow, or provider assistance. No provider settings or mandates were changed during this review.

## Google sign-in branding

The source uses Google's **web-server authorization-code flow** (`/auth/google` → Google → `/auth/google/callback`), requesting `openid email profile` with state, nonce, and PKCE. Google ID tokens are verified for the client and nonce before login. `prompt=select_account` requests the account chooser; it does not set its brand. The Android package name and Digital Asset Links certificate do not name this web OAuth client.

Google states that external production apps need verified branding to display the app name/logo; otherwise users may see the application domain. The reported hosted-domain presentation is consistent with unverified/unpublished branding, but the console's actual state was not accessible and has not been asserted. Google can still display the domain as legitimate security information after branding is verified. [Google: Manage OAuth App Branding](https://support.google.com/cloud/answer/15549049?hl=en)

### Google Cloud Console steps for the owner

Use the existing production OAuth project's **Google Auth Platform**; do not create a replacement client or rotate its secret just to change the display name.

1. In **Branding**, set the application name to **Ennoic**, a monitored support email and developer contact, and Ennoic's actual existing logo. Provide the public product home page, privacy-policy URL and terms URL. The home page should describe the product and link its legal pages rather than being only a sign-in form.
2. Review **Audience** and **Data Access**. Keep only the sign-in scopes already needed by Ennoic. Check the publishing status and test-user restrictions appropriate to the intended audience.
3. Verify ownership of the authorized domains through a project owner/editor's Google Search Console account. Use only domains you can verify. If a shared hosting domain prevents suitable ownership/branding verification, use an owned custom domain; do not claim ownership of Render's shared parent domain.
4. Complete **Verify Branding**, resolve any findings, then **Publish branding**. Saving a draft alone does not update published branding. The verification result is controlled by Google. [Google brand verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/brand-verification)
5. In **Clients**, inspect the existing **Web application** client. Its production authorized redirect URI must match exactly:

   ```text
   https://ennoic.onrender.com/auth/google/callback
   ```

   This server-side flow does not need JavaScript origins to set its display name. If origins are configured for another supported browser integration, use the actual scheme/host/port, e.g. `https://ennoic.onrender.com`, with no callback path. Do not add wildcard redirect URIs. [Google web-server OAuth setup](https://developers.google.com/identity/protocols/oauth2/web-server)

### Future custom-domain migration

Plan this as a separate coordinated origin migration: configure the owned domain and HTTPS in Render, verify it with Google, add its exact callback URI before switching, and change Ennoic's trusted public origin configuration to match. Review host/origin/CSRF protections, legal links, Paddle approved checkout domains and success URLs, PWA URLs, and Android host/Digital Asset Links together. Cookies are origin-bound, so existing users may need to sign in again on the new domain. A TWA host change requires an updated APK and association on that new host; keep a tested transition for existing installs. Do not simply redirect the old callback to a different host mid-flow.

### What remains manual

No Google Console or Paddle dashboard settings were changed. Console verification/publishing, exact production price inspection, provider explanations for the reported cap/date, and physical-device completion of Google sign-in and Paytm return navigation remain manual. No code can safely conceal Google's hosted-domain security text or override a provider's mandate terms with marketing copy.
