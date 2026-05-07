# Superalink Checkout Tool

Self-hosted Superalink eSIM checkout helper for `supera.onlypast.com`.

Features:

- In-page destination / SKU / currency selector.
- Pulls product catalog from Superalink storefront API.
- Creates a fresh Superalink checkout order per click.
- Applies affiliate coupon code `HAN000000` by default.
- Stores checkout/payment secrets server-side using opaque short-lived tokens.
- Custom payment page with Stripe Payment Element, Stripe Express Checkout, and PayPal Buttons.
- Optional native checkout proxy fallback.

## Run

```bash
export STRIPE_PK='pk_live_xxx'
export PAYPAL_CLIENT_ID='xxx'
python3 superalink_checkout_tool.py
```

The service listens on `0.0.0.0:53333`.

## Caddy example

See `Caddyfile.example`.

## Notes

- Do not commit real checkout tokens, payment intent client secrets, cookies, or order IDs.
- `/pay?t=...` tokens are in-memory and expire after 30 minutes.
- Apple Pay requires Safari + wallet + Stripe/Apple Pay merchant domain verification for the serving domain.
