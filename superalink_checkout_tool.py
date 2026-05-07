#!/usr/bin/env python3
import base64
import html
import json
import os
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests

STORE = "https://storefront.api.superalink.com"
WWW = "https://www.superalink.com"
LOCAL_API_BASE = "https://supera.onlypast.com/storefront-api"
DEFAULT_URL = "https://www.superalink.com/cn/esim/china-mainland?affiliate_code=HAN000000&duration=5&option=unlimited&promo=affiliate-influencer&utm_source=affiliate"
DEFAULT_COUNTRY_CODE = "CN"
DEFAULT_LOCALE = "cn"
DEFAULT_CURRENCY = "THB"
DEFAULT_DURATION = "5"
DEFAULT_OPTION = "unlimited"
DEFAULT_SKU = "CN-5GB_UNLIMITED-5GB-5-DAYS"
DEST_SLUGS = {
    "china-mainland": "CN",
    "china": "CN",
    "taiwan": "TW",
    "hong-kong": "HK",
    "japan": "JP",
    "korea": "KR",
    "south-korea": "KR",
    "singapore": "SG",
    "thailand": "TH",
    "malaysia": "MY",
    "indonesia": "ID",
    "vietnam": "VN",
    "philippines": "PH",
    "united-states": "US",
    "usa": "US",
    "united-kingdom": "GB",
}

COUNTRY_SLUGS = {v: k for k, v in DEST_SLUGS.items()}
COUNTRY_SLUGS.update({
    "AU": "australia",
    "NZ": "new-zealand",
    "US": "united-states",
    "GB": "united-kingdom",
    "HK": "hong-kong",
    "MO": "macau",
    "SG": "singapore",
    "JP": "japan",
    "KR": "south-korea",
    "TH": "thailand",
    "MY": "malaysia",
    "ID": "indonesia",
    "VN": "vietnam",
    "PH": "philippines",
    "TW": "taiwan",
    "CN": "china-mainland",
})

DISCOUNT_CAPS = {
    "THB": {"amount": 175, "symbol": "฿", "decimals": 2},
    "EUR": {"amount": 4, "symbol": "€", "decimals": 2},
    "USD": {"amount": 5, "symbol": "$", "decimals": 2},
    "GBP": {"amount": 4, "symbol": "£", "decimals": 2},
    "KRW": {"amount": 6750, "symbol": "₩", "decimals": 0},
    "JPY": {"amount": 775, "symbol": "¥", "decimals": 0},
    "SGD": {"amount": 6.75, "symbol": "S$", "decimals": 2},
    "CNY": {"amount": 36.25, "symbol": "¥", "decimals": 2},
    "IDR": {"amount": 80000, "symbol": "Rp", "decimals": 0},
}

CNY_RATES = {"THB": 0.21, "GBP": 9.15, "AUD": 4.70, "SGD": 5.55, "USD": 7.20, "HKD": 0.92, "TWD": 0.23, "JPY": 0.047, "CNY": 1, "EUR": 7.75, "KRW": 0.0052, "IDR": 0.00045}
CURRENCY_SYMBOLS = {"THB": "฿", "GBP": "£", "AUD": "A$", "SGD": "S$", "USD": "$", "HKD": "HK$", "TWD": "NT$", "JPY": "¥", "CNY": "¥", "EUR": "€", "KRW": "₩", "IDR": "Rp"}
CURRENCY_DECIMALS = {"JPY": 0, "KRW": 0, "IDR": 0}

LOCAL_REFERENCE_CURRENCIES = {
    "CN": "CNY",
    "HK": "HKD",
    "MO": "HKD",
    "HK_MO": "HKD",
    "TW": "TWD",
    "JP": "JPY",
    "KR": "KRW",
    "TH": "THB",
    "SG": "SGD",
    "ID": "IDR",
    "AU": "AUD",
    "NZ": "AUD",
    "GB": "GBP",
    "US": "USD",
    "US_CA": "USD",
    "EU_33": "EUR",
}

def local_reference_currency(country_code):
    cc = (country_code or DEFAULT_COUNTRY_CODE).upper()
    return LOCAL_REFERENCE_CURRENCIES.get(cc, "CNY")

def country_slug(country_code):
    cc = (country_code or DEFAULT_COUNTRY_CODE).upper()
    return COUNTRY_SLUGS.get(cc) or cc.lower().replace("_", "-")

def format_price(currency, amount):
    decimals = CURRENCY_DECIMALS.get(currency, 2)
    symbol = CURRENCY_SYMBOLS.get(currency, currency + " ")
    if amount is None:
        return "--"
    if decimals == 0:
        return f"{symbol}{int(round(amount))}"
    return f"{symbol}{amount:.2f}"

def cap_discounted_prices(product):
    prices = product.get("price", {}) or {}
    out = {}
    for cur, p in prices.items():
        if cur not in DISCOUNT_CAPS:
            continue
        amount = p.get("amount") if isinstance(p, dict) else None
        if amount is None:
            continue
        cap = (DISCOUNT_CAPS.get(cur) or {}).get("amount", 0)
        final = max(0, round(float(amount) - float(cap), 2))
        decimals = p.get("decimals", CURRENCY_DECIMALS.get(cur, 2)) if isinstance(p, dict) else CURRENCY_DECIMALS.get(cur, 2)
        out[cur] = {
            "amount": int(round(final)) if decimals == 0 else final,
            "symbol": (DISCOUNT_CAPS.get(cur) or {}).get("symbol") or p.get("symbol") or CURRENCY_SYMBOLS.get(cur, cur),
            "inUse": bool(p.get("inUse")) if isinstance(p, dict) else False,
            "decimals": decimals,
            "display": format_price(cur, final),
            "formattedAmount": str(int(round(final))) if decimals == 0 else f"{final:.2f}",
            "discountCap": cap,
            "discountDisplay": format_price(cur, cap),
        }
    return out


def country_code_from_url(page_url):
    p = urlparse(page_url or "")
    parts = [x for x in p.path.split("/") if x]
    if "esim" in parts:
        i = parts.index("esim")
        if i + 1 < len(parts):
            slug = parts[i + 1].lower()
            if slug in DEST_SLUGS:
                return DEST_SLUGS[slug]
    if "destination" in parts:
        # /cn/destination/aff/HAN000000 is an affiliate landing page, not a single SKU.
        return None
    return None


def sku_country_code(sku):
    if sku and "-" in sku:
        return sku.split("-", 1)[0].upper()
    return None


DEFAULT_COUPON = "HAN000000"
STRIPE_PK = os.environ.get("STRIPE_PK", "")
PAYPAL_CLIENT_ID = os.environ.get("PAYPAL_CLIENT_ID", "")
TOKENS = {}
TOKEN_TTL = 1800


def store_token(data):
    cleanup_tokens()
    token = secrets.token_urlsafe(32)
    TOKENS[token] = {"expires": time.time() + TOKEN_TTL, "data": data}
    return token


def load_token(token):
    cleanup_tokens()
    item = TOKENS.get(token)
    if not item:
        raise ValueError("token expired or not found")
    return item["data"]


def cleanup_tokens():
    now = time.time()
    for k in list(TOKENS.keys()):
        if TOKENS[k].get("expires", 0) < now:
            TOKENS.pop(k, None)


def encode_payload(data):
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_payload(token):
    padded = token + "=" * (-len(token) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode()).decode())


def page_headers(page_url=DEFAULT_URL, locale=DEFAULT_LOCALE):
    p = urlparse(page_url or DEFAULT_URL)
    path = p.path + (("?" + p.query) if p.query else "")
    return {
        "Content-Type": "application/json",
        "Accept-Language": locale,
        "Origin": WWW,
        "Referer": page_url or DEFAULT_URL,
        "User-Agent": "Mozilla/5.0 (Superalink checkout prefill; +local)",
        "X-Page-URL": page_url or DEFAULT_URL,
        "X-Page-Path": path,
        "X-Page-Origin": f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else WWW,
    }


def api_error(resp):
    try:
        body = resp.json()
    except Exception:
        body = resp.text
    return {"status_code": resp.status_code, "body": body}


def normalize_option(option):
    if not option:
        return None
    o = str(option).lower().strip()
    if o in ("unlimited", "无限", "不限量"):
        return "UNLIMITED"
    if o in ("quota", "regular", "fixed", "流量"):
        return "QUOTA"
    return o.upper()


def duration_days(product):
    value = product.get("dataPlan", {}).get("data", {}).get("duration", {}).get("value")
    unit = product.get("dataPlan", {}).get("data", {}).get("duration", {}).get("unit")
    if unit == "MILLISECONDS" and value is not None:
        return round(value / 86400000)
    return None


def product_data_amount(product):
    dp = product.get("dataPlan", {})
    if dp.get("option") == "UNLIMITED":
        data = (dp.get("FUP") or {}).get("data") or {}
    else:
        data = dp.get("data", {}).get("data", {})
    return data.get("amount"), data.get("unit")


def storefront_visible_product(product):
    """Keep only the simplified storefront choices the official product page exposes.

    The raw API contains many hidden/deep SKU combinations. On the CN storefront
    offer, the visible plan family is daily 5GB unlimited for 5-30 days.
    """
    amount, unit = product_data_amount(product)
    days = duration_days(product)
    return (
        product.get("dataPlan", {}).get("option") == "UNLIMITED"
        and float(amount or 0) == 5
        and str(unit).upper() == "GB"
        and days in (5, 6, 7, 10, 12, 15, 20, 30)
    )


def discounted_price_for_product(product, currency, coupon=DEFAULT_COUPON):
    return (cap_discounted_prices(product) or {}).get(currency)


def catalog_for_country(country_code):
    r = requests.get(
        f"{STORE}/products",
        params={"country_code": country_code},
        headers={"Accept-Language": DEFAULT_LOCALE, "User-Agent": "Mozilla/5.0"},
        timeout=25,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"products API failed: {api_error(r)}")
    products = []
    for g in r.json():
        products.extend(g.get("products", []))
    out = []
    for p in products:
        if not storefront_visible_product(p):
            continue
        amount, unit = product_data_amount(p)
        discounted = cap_discounted_prices(p)
        out.append({
            "sku": p.get("sku"),
            "kind": p.get("kind"),
            "option": p.get("dataPlan", {}).get("option"),
            "duration_days": duration_days(p),
            "data_amount": amount,
            "data_unit": unit,
            "prices": p.get("price", {}),
            "discounted_prices": discounted,
            "country_slug": country_slug(country_code),
            "reference_currency": local_reference_currency(country_code),
        })
    out.sort(key=lambda x: (x.get("duration_days") or 9999, x.get("option") or "", x.get("sku") or ""))
    return out


def choose_product(country_code="CN", duration=5, option="unlimited", data_amount=None, data_unit=None, sku=None):
    r = requests.get(
        f"{STORE}/products",
        params={"country_code": country_code},
        headers={"Accept-Language": DEFAULT_LOCALE, "User-Agent": "Mozilla/5.0"},
        timeout=25,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"products API failed: {api_error(r)}")
    products = []
    for g in r.json():
        products.extend(g.get("products", []))
    if sku:
        for p in products:
            if p.get("sku") == sku:
                return p
        raise ValueError(f"SKU not found: {sku}")
    opt = normalize_option(option)
    candidates = []
    for p in products:
        if opt and p.get("dataPlan", {}).get("option") != opt:
            continue
        if duration is not None and duration_days(p) != int(duration):
            continue
        amount, unit = product_data_amount(p)
        if data_amount not in (None, ""):
            try:
                if float(amount) != float(data_amount):
                    continue
            except Exception:
                continue
        if data_unit and str(unit).upper() != str(data_unit).upper():
            continue
        candidates.append(p)
    if not candidates:
        raise ValueError("No product matched. Try explicit sku.")
    if opt == "UNLIMITED" and data_amount in (None, ""):
        for p in candidates:
            amount, unit = product_data_amount(p)
            if amount == 5 and unit == "GB":
                return p
    return sorted(candidates, key=lambda p: p.get("price", {}).get("USD", {}).get("amount", 10**9))[0]


def bind_session_cookies(sess, buyer_session_id, locale=DEFAULT_LOCALE):
    for domain in [".superalink.com", "storefront.api.superalink.com", "www.superalink.com"]:
        sess.cookies.set("splnk_checkout_session", buyer_session_id, domain=domain, path="/")
        sess.cookies.set("NEXT_LOCALE", locale, domain=domain, path="/")


def create_checkout(params):
    page_url = params.get("url") or DEFAULT_URL
    qs = parse_qs(urlparse(page_url).query)
    affiliate_code = params.get("affiliate_code") or (qs.get("affiliate_code") or [DEFAULT_COUPON])[0]
    coupon = params.get("coupon", affiliate_code or DEFAULT_COUPON)
    currency = params.get("currency") or DEFAULT_CURRENCY
    duration = params.get("duration") or (qs.get("duration") or [DEFAULT_DURATION])[0]
    option = params.get("option") or (qs.get("option") or [DEFAULT_OPTION])[0]
    url_country = country_code_from_url(page_url)
    sku = params.get("sku") or None
    country_code = params.get("country_code") or sku_country_code(sku) or url_country or DEFAULT_COUNTRY_CODE
    locale = params.get("locale") or DEFAULT_LOCALE
    if not sku and country_code == DEFAULT_COUNTRY_CODE and duration == DEFAULT_DURATION and option == DEFAULT_OPTION and not url_country:
        sku = DEFAULT_SKU
    product = choose_product(country_code, int(duration) if duration else None, option, params.get("data_amount"), params.get("data_unit"), sku)
    sess = requests.Session()
    headers = page_headers(page_url, locale)
    payload = {"sku": product["sku"], "qty": int(params.get("qty") or 1), "currency": currency, "isExtension": False}
    if coupon:
        payload["coupon"] = coupon
    r = sess.post(f"{STORE}/v2/checkout", json=payload, headers=headers, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"create checkout failed: {api_error(r)}")
    order = r.json()["order"]
    buyer_session_id = order["buyer"]["sessionID"]
    bind_session_cookies(sess, buyer_session_id, locale)
    return sess, headers, product, order, buyer_session_id, payload


def update_recipient_email(sess, headers, order_id, email, subscribe=False):
    if not email:
        return None
    payload = {
        "voucherRecipientEmail": email,
        "voucherRecipientIsSubscribingToNewsletter": bool(subscribe),
    }
    r = sess.patch(f"{STORE}/v2/checkout/{order_id}", json=payload, headers=headers, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"update recipient email failed: {api_error(r)}")
    return r.json().get("order")


def get_order(sess, headers, order_id):
    r = sess.get(f"{STORE}/v2/checkout/{order_id}", headers=headers, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"get order failed: {api_error(r)}")
    return r.json().get("order") or r.json()


def get_intents(sess, headers, order_id):
    r = sess.get(f"{STORE}/v2/checkout/{order_id}/payment-intents", headers=headers, timeout=30)
    if r.status_code >= 400:
        return []
    return r.json()


def update_order_full(sess, headers, order, email="", phone="", subscribe=True):
    """Update recipient/contact fields only.

    Superalink rejects currency/SKU changes after a payment intent exists. The old
    full payload included currency and could accidentally default to THB because
    checkout orders often omit a top-level `currency` field. Keep this PATCH
    narrow so it cannot mutate currency after intents are created.
    """
    if not email and not phone:
        return order
    payload = {
        "voucherRecipientEmail": email,
        "voucherRecipientPhone": phone or None,
        "voucherRecipientIsSubscribingToNewsletter": bool(subscribe),
    }
    r = sess.patch(f"{STORE}/v2/checkout/{order['uniqueId']}", json=payload, headers=headers, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"update order failed: {api_error(r)}")
    return r.json().get("order")


def make_paypal_intent(sess, headers, order_id):
    r = sess.post(f"{STORE}/v2/checkout/{order_id}/payment-intents?paymentMethod=paypal", headers=headers, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"make paypal intent failed: {api_error(r)}")
    return r.json()


def capture_intent(sess, headers, order_id, payment_intent_id):
    r = sess.post(
        f"{STORE}/v2/checkout/{order_id}/payment-intents/{payment_intent_id}/capture",
        headers=headers,
        timeout=30,
    )
    if r.status_code >= 400:
        return {"ok": False, "error": api_error(r)}
    return {"ok": True, "intent": (r.json() or {}).get("intent") or r.json()}


def authorize_capture(sess, headers, order_id, payment_intent_id):
    r = sess.post(
        f"{STORE}/v2/checkout/{order_id}/payment-intents/{payment_intent_id}/authorize-capture",
        headers=headers,
        timeout=30,
    )
    if r.status_code >= 400:
        return {"ok": False, "error": api_error(r)}
    return {"ok": True, "intent": (r.json() or {}).get("intent") or r.json()}


def callback_query(payment_method, buyer_session_id, extra=None):
    data = {"paymentMethod": payment_method, "session": buyer_session_id}
    if extra:
        data.update(extra)
    return "?" + urlencode(data)


def active_price_from_intent(intent, currency):
    prices = (intent or {}).get("prices") or {}
    for bucket in ("net", "gross"):
        price = (prices.get(bucket) or {}).get(currency)
        if price:
            return price
        for candidate in (prices.get(bucket) or {}).values():
            if isinstance(candidate, dict) and candidate.get("inUse"):
                return candidate
    return None


def result_from_params(params):
    sess, headers, product, order, buyer_session_id, payload = create_checkout(params)
    order_id = order["uniqueId"]
    email = (params.get("email") or params.get("recipient_email") or "").strip()
    if email:
        updated_order = update_recipient_email(
            sess,
            headers,
            order_id,
            email,
            str(params.get("subscribe", "")).lower() in ("1", "true", "yes", "y"),
        )
        if updated_order:
            order = updated_order
    # Re-read payment intents only for amount verification/display; the user will pay on Superalink native page.
    intents = get_intents(sess, headers, order_id)
    stripe = next((i for i in intents if i.get("methodIdentifier") == "stripe"), None)
    amount, unit = product_data_amount(product)
    checkout_price = active_price_from_intent(stripe, payload["currency"]) or product.get("price", {}).get(payload["currency"], product.get("price", {}).get("USD"))
    locale = params.get("locale") or DEFAULT_LOCALE
    checkout_url = f"{WWW}/{locale}/checkout/{order_id}?affiliate_code={payload.get('coupon') or DEFAULT_COUPON}&duration={params.get('duration') or DEFAULT_DURATION}&option={params.get('option') or DEFAULT_OPTION}&promo=affiliate-influencer&utm_source=affiliate&currency={payload['currency']}&coupon={payload.get('coupon') or DEFAULT_COUPON}"
    client_secret = stripe.get("meta", {}).get("clientSecret") if stripe else None
    stripe_intent_id = stripe.get("id") if stripe else None
    paypal_intent = next((i for i in intents if i.get("methodIdentifier") == "paypal"), None)
    token_data = {
        "checkout_url": checkout_url,
        "order_id": order_id,
        "email": email or None,
        "coupon": payload.get("coupon"),
        "amount": (checkout_price or {}).get("display"),
        "currency": payload["currency"],
        "client_secret": client_secret,
        "stripe_intent_id": stripe_intent_id,
        "paypal_intent_id": paypal_intent.get("id") if paypal_intent else None,
        "paypal_order_id": (paypal_intent.get("meta") or {}).get("orderId") if paypal_intent else None,
        "cookie_name": "splnk_checkout_session",
        "cookie_value": buyer_session_id,
        "product": {
            "sku": product.get("sku"),
            "country_code": product.get("sku", "").split("-", 1)[0],
            "kind": product.get("kind"),
            "duration_days": duration_days(product),
            "option": product.get("dataPlan", {}).get("option"),
            "fup_or_data": {"amount": amount, "unit": unit},
            "price": checkout_price,
            "gross_price": product.get("price", {}).get(payload["currency"], product.get("price", {}).get("USD")),
        },
    }
    token = store_token(token_data)
    return {
        "ok": True,
        "created_at": int(time.time()),
        "checkout_url": checkout_url,
        "pay_url": f"/pay?t={token}",
        "native_url": f"/go?t={token}",
        "order_id": order_id,
        "email": email or None,
        "recipient": order.get("recipient"),
        "session_cookie": {"name": "splnk_checkout_session", "value": "[REDACTED]", "domain": ".superalink.com"},
        "product": {
            "sku": product.get("sku"),
            "country_code": product.get("sku", "").split("-", 1)[0],
            "kind": product.get("kind"),
            "duration_days": duration_days(product),
            "option": product.get("dataPlan", {}).get("option"),
            "fup_or_data": {"amount": amount, "unit": unit},
            "price": checkout_price,
            "gross_price": product.get("price", {}).get(payload["currency"], product.get("price", {}).get("USD")),
        },
        "coupon_sent": payload.get("coupon"),
        "note": "已创建 Superalink 原生 checkout；浏览器会在 supera.onlypast.com 同域代理页写入 session 后打开付款页，以便优惠券和付款方式正常加载。",
    }


def flow_html(result):
    return f"""<!doctype html><meta charset='utf-8'><title>进入 Superalink 原付款页</title>
<style>body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:720px;margin:40px auto;padding:0 16px}}.box{{border:1px solid #ddd;border-radius:12px;padding:18px;margin:16px 0}}a.btn{{display:inline-block;background:#0a7cff;color:white;padding:14px 22px;border-radius:10px;text-decoration:none;font-size:18px}}code{{background:#f5f5f5;padding:2px 4px;border-radius:4px}}.muted{{color:#666;font-size:14px}}</style>
<h2>订单已创建</h2>
<div class=box>
  <p><b>邮箱：</b>{html.escape(result.get('email') or '')}</p>
  <p><b>优惠券：</b>{html.escape(result.get('coupon_sent') or '')}</p>
  <p><b>订单：</b><code>{html.escape(result['order_id'])}</code></p>
  <p><b>产品：</b>{html.escape(result['product']['sku'])} / {html.escape((result['product'].get('price') or {}).get('display') or '')}</p>
</div>
<p><a class=btn href="{html.escape(result['pay_url'])}">进入 Superalink 原付款页</a></p>
<p class=muted>支付页面使用 Superalink 官方 checkout，因此会保留原来的银行卡、PayPal 等所有支付方式。本工具只负责预创建订单、预填邮箱和优惠券。</p>
<script>setTimeout(()=>{{ location.href = {json.dumps(result['pay_url'])}; }}, 800);</script>"""


def pay_html(data):
    safe_amount = html.escape(data.get("amount") or "฿25.00")
    safe_coupon = html.escape(data.get("coupon") or DEFAULT_COUPON)
    product = data.get("product") or {}
    title = html.escape(f"Superalink {product.get('country_code') or ''} eSIM".strip())
    sku = html.escape(product.get("sku") or "")
    days = product.get("duration_days") or ""
    fup = product.get("fup_or_data") or {}
    fup_text = html.escape(f"{days}天・{product.get('option') or ''}・{fup.get('amount') or ''}{fup.get('unit') or ''}".replace("UNLIMITED", "无限流量").replace("QUOTA", "固定流量"))
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Superalink 自建付款</title><script src="https://js.stripe.com/v3/"></script>
<style>body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#f6f7f9;margin:0;color:#111}}.wrap{{max-width:560px;margin:18px auto;padding:16px}}.card{{background:#fff;border-radius:16px;box-shadow:0 8px 28px rgba(0,0,0,.08);padding:20px;margin-bottom:14px}}h2{{margin:0 0 8px}}label{{display:block;font-weight:650;margin:14px 0 6px}}input{{width:100%;box-sizing:border-box;padding:13px;border:1px solid #ddd;border-radius:10px;font-size:16px}}button{{width:100%;padding:15px;border:0;border-radius:10px;background:#0a7cff;color:white;font-size:17px;margin-top:16px}}button:disabled{{opacity:.55}}.muted{{color:#666;font-size:14px;line-height:1.55}}.tip{{background:#fff7ed;border:1px solid #fed7aa;border-radius:12px;padding:10px 12px;margin:10px 0 12px;color:#7c2d12;font-size:13px;line-height:1.55}}.tip b{{color:#9a3412}}.row{{display:flex;justify-content:space-between;margin:8px 0}}.ok{{color:#0a8a3a}}.err{{color:#c62828;white-space:pre-wrap}}#payment-element{{margin-top:12px}}#paypal-status{{margin:10px 0 8px}}.paypal-fallback{{background:#ffc439;color:#111;font-weight:700}}</style></head>
<body><div class=wrap>
<div class=card><h2>{title}</h2><p class=muted>{fup_text} · SKU {sku}</p>
<div class=row><span>优惠券</span><b>{safe_coupon}</b></div><div class=row><span>应付</span><b>{safe_amount}</b></div><div id=status class="muted">正在加载银行卡支付组件...</div></div>
<div class=card><form id=pay-form><label>接收 eSIM 的邮箱</label><div class="tip"><b>邮箱提示：</b>请保证使用未购买过的邮箱，否则可能会移除优惠券，被反薅。未使用的 eSIM 通常可无理由退款，具体以 Superalink 官方规则为准。</div><input id=email type=email autocomplete=email placeholder="you@example.com" required><div id="wallet-status" class="muted">正在检测 Apple Pay / Google Pay / Link...</div><div id="express-element"></div><div id="paypal-status" class="muted">正在加载 PayPal...</div><div id="paypal-buttons"></div><button id="paypal-fallback" type="button" class="paypal-fallback">PayPal 支付 {safe_amount}</button><div id="payment-element"></div><button id=submit disabled>银行卡 / 钱包支付 {safe_amount}</button><div id=message class=err></div></form><p class=muted>已尽量启用：银行卡、Apple Pay、Google Pay、Link、PayPal。Apple/Google Pay 是否显示取决于用户设备、浏览器和钱包环境。</p><p class=muted>如果自建支付失败，可点这里回退到原生代理页：<a id="native-link" href="#">打开原生页</a></p></div>
</div><script>
const DATA={json.dumps(data, ensure_ascii=False)};
document.getElementById('native-link').href='/go?t='+encodeURIComponent(DATA.token);
const stripe=Stripe({json.dumps(STRIPE_PK)});
let elements;
function minorAmount(display,currency){{let n=parseFloat(String(display||'25').replace(/[^0-9.]/g,''));return ['jpy','krw'].includes(String(currency).toLowerCase())?Math.round(n):Math.round(n*100);}}
async function init(){{try{{
  if(!DATA.client_secret) throw new Error('缺少 Stripe client_secret');
  elements=stripe.elements({{clientSecret:DATA.client_secret,appearance:{{theme:'stripe',variables:{{colorPrimary:'#F47325'}}}}}});
  try{{
    const express=elements.create('expressCheckout',{{buttonHeight:48,buttonTheme:{{applePay:'black',googlePay:'black'}},buttonType:{{applePay:'buy',googlePay:'buy'}},paymentMethods:{{applePay:'always',googlePay:'always',link:'auto'}}}});
    express.mount('#express-element');
    express.on('ready',(ev)=>{{
      const pm=(ev&&ev.availablePaymentMethods)||{{}};
      const names=[];
      if(pm.applePay) names.push('Apple Pay');
      if(pm.googlePay) names.push('Google Pay');
      if(pm.link) names.push('Link');
      const ws=document.getElementById('wallet-status');
      if(names.length) ws.innerHTML='<span class=ok>可用钱包：'+names.join(' / ')+'</span>';
      else ws.textContent='当前浏览器未返回可用 Apple Pay / Google Pay / Link。Apple Pay 还要求本域名通过 Stripe/Apple Pay 商户域名验证。';
    }});
    express.on('confirm', async()=>{{await runStripeConfirm();}});
  }}catch(e){{document.getElementById('wallet-status').className='err';document.getElementById('wallet-status').textContent='钱包组件不可用：'+(e.message||e);console.warn('express checkout unavailable',e)}}
  const paymentElement=elements.create('payment',{{business:{{name:'Superalink'}},wallets:{{applePay:'auto',googlePay:'auto'}},layout:{{type:'accordion',defaultCollapsed:false,radios:true}}}});
  paymentElement.mount('#payment-element');
  document.getElementById('submit').disabled=false;document.getElementById('status').innerHTML='<span class=ok>付款组件已加载，金额 '+(DATA.amount||'')+'</span>';
}}catch(e){{document.getElementById('status').className='err';document.getElementById('status').textContent='加载失败：'+e.message;}}}}
async function runStripeConfirm(){{
  const email=document.getElementById('email').value.trim();
  if(!email) throw new Error('请先填写接收 eSIM 的邮箱');
  const pre=await fetch('/api/prepay',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{t:DATA.token,email,method:'stripe'}})}}).then(r=>r.json());
  if(!pre.ok) throw new Error(pre.error||'prepay failed');
  const ret=location.origin+'/api/stripe-callback'+pre.callback;
  const res=await stripe.confirmPayment({{elements,confirmParams:{{return_url:ret}},redirect:'always'}});
  if(res.error) throw new Error(res.error.message||res.error.code||'Stripe error');
}}
async function loadPaypalSdk(){{
  return new Promise((resolve,reject)=>{{
    if(window.paypal) return resolve(window.paypal);
    const cur=(DATA.currency==='KRW')?'USD':(DATA.currency||'USD');
    const s=document.createElement('script');
    s.src='https://www.paypal.com/sdk/js?client-id='+encodeURIComponent({json.dumps(PAYPAL_CLIENT_ID)})+'&currency='+encodeURIComponent(cur)+'&intent=capture&components=buttons';
    s.onload=()=>window.paypal?resolve(window.paypal):reject(new Error('PayPal SDK loaded but window.paypal missing'));
    s.onerror=()=>reject(new Error('PayPal SDK 加载失败'));
    document.head.appendChild(s);
    setTimeout(()=>{{if(!window.paypal) reject(new Error('PayPal SDK 加载超时'))}},15000);
  }});
}}
async function startPaypalFallback(){{
  const btn=document.getElementById('paypal-fallback');
  const email=document.getElementById('email').value.trim();
  if(!email){{document.getElementById('message').textContent='请先填写接收 eSIM 的邮箱';return;}}
  btn.disabled=true;document.getElementById('message').textContent='正在创建 PayPal 订单...';
  try{{
    const r=await fetch('/api/paypal/create',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{t:DATA.token,email}})}}).then(r=>r.json());
    if(!r.ok) throw new Error(r.error||'paypal create failed');
    document.getElementById('message').textContent='PayPal 订单已创建；如果按钮没有弹出，请检查当前网络是否能访问 paypal.com。';
  }}catch(e){{document.getElementById('message').textContent='PayPal 创建失败：'+(e.message||e)}}finally{{btn.disabled=false;}}
}}
async function setupPaypal(){{const st=document.getElementById('paypal-status');try{{
  await loadPaypalSdk();
  st.innerHTML='<span class=ok>PayPal 已加载，可直接点击 PayPal 按钮</span>';
  paypal.Buttons({{
    style:{{layout:'vertical',color:'gold',shape:'rect',label:'paypal'}},
    createOrder:async()=>{{const email=document.getElementById('email').value.trim(); if(!email) throw new Error('请先填写接收 eSIM 的邮箱'); const r=await fetch('/api/paypal/create',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{t:DATA.token,email}})}}).then(r=>r.json()); if(!r.ok) throw new Error(r.error||'paypal create failed'); return r.paypal_order_id;}},
    onApprove:async(data)=>{{const r=await fetch('/api/paypal/capture',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{t:DATA.token,paypal_order_id:data.orderID}})}}).then(r=>r.json()); if(r.ok) location.href='/api/stripe-callback?paymentMethod=paypal&session='+encodeURIComponent(DATA.cookie_value||''); else document.getElementById('message').textContent=r.error||'PayPal capture failed';}},
    onError:(err)=>{{document.getElementById('message').textContent='PayPal 错误：'+(err.message||err)}}
  }}).render('#paypal-buttons');
}}catch(e){{st.className='err';st.textContent='PayPal 按钮加载失败：'+(e.message||e)+'。下方黄色 PayPal 按钮会尝试先创建官方 PayPal 订单；若仍无弹窗，多半是当前网络拦截 paypal.com。';console.warn('paypal unavailable',e)}}}}
document.getElementById('paypal-fallback').addEventListener('click',startPaypalFallback);
document.getElementById('pay-form').addEventListener('submit',async(e)=>{{e.preventDefault();const btn=document.getElementById('submit');btn.disabled=true;document.getElementById('message').textContent='正在补齐订单信息并进入付款...';try{{
  await runStripeConfirm();
}}catch(err){{document.getElementById('message').textContent=err.message;btn.disabled=false;}}}});
init(); setupPaypal();
</script></body></html>"""


INDEX_HTML = r"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Superalink 自建付款页</title>
<style>
body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#f6f7f9;margin:0;padding:18px;color:#111}.wrap{max-width:680px;margin:18px auto;background:white;border-radius:16px;box-shadow:0 8px 30px rgba(0,0,0,.08);padding:22px}h2{margin-top:0}label{display:block;margin:14px 0 6px;font-weight:650}select,input{width:100%;box-sizing:border-box;padding:13px;border:1px solid #ddd;border-radius:10px;font-size:16px;background:#fff}button{margin-top:18px;width:100%;padding:14px 18px;border:0;border-radius:10px;background:#0a7cff;color:white;font-size:17px;cursor:pointer}button:disabled{opacity:.55;cursor:not-allowed}.muted{color:#666;font-size:14px;line-height:1.55}.pill{display:inline-block;background:#eef5ff;border:1px solid #cfe4ff;padding:4px 8px;border-radius:999px;font-size:13px;margin:3px 4px 3px 0}.summary{background:#f8fafc;border:1px solid #e5e7eb;border-radius:12px;padding:12px;margin-top:14px}.notice{background:#fff7ed;border:1px solid #fed7aa;border-radius:12px;padding:12px 14px;margin:14px 0;color:#7c2d12;font-size:13px;line-height:1.6}.notice b{color:#9a3412}.links{display:grid;gap:8px;background:#f0f9ff;border:1px solid #bae6fd;border-radius:12px;padding:12px 14px;margin:14px 0;font-size:14px}.links a{color:#0369a1;text-decoration:none;font-weight:650;word-break:break-all}.links a:hover{text-decoration:underline}.row{display:flex;justify-content:space-between;gap:12px;margin:6px 0}code{font-size:12px;word-break:break-all}
</style></head><body>
<div class="wrap">
<h2>Superalink 自建付款页</h2>
<p class="muted">实现站内自选：目的地、SKU/套餐、币种都在本页选择，不需要跳去官网或粘链接。默认优惠券 <b>HAN000000</b>。</p>
<div class="links">
  <div>GitHub：<a href="https://github.com/mhan24/superalink-checkout-tool" target="_blank" rel="noopener noreferrer">https://github.com/mhan24/superalink-checkout-tool</a></div>
  <div>TG 群组：<a href="https://t.me/setupode" target="_blank" rel="noopener noreferrer">https://t.me/setupode</a></div>
  <div>TG 频道：<a href="https://t.me/setup0de" target="_blank" rel="noopener noreferrer">https://t.me/setup0de</a></div>
</div>
<div class="notice"><b>免责声明：</b>本站只是 Superalink eSIM 的自助下单辅助入口，商品、价格、支付、订单履约、售后与退款均以 Superalink 官方及支付服务商实际结果为准。请在付款前自行核对套餐、目的地、天数、流量、币种和最终金额；本站不保证所有支付方式在所有设备/浏览器中都可用，也不提供绕过风控或安全验证的服务。</div>
<form id="orderForm" method="GET" action="/flow" onsubmit="btn.disabled=true;status.textContent='正在按所选 SKU / 币种创建订单...';">
<label>目的地</label>
<select id="country" name="country_code">
  <option value="CN">中国大陆（China-Mainland）CN</option>
  <option value="TW">台湾（Taiwan）TW</option>
  <option value="HK">香港（Hong Kong）HK</option>
  <option value="JP">日本（Japan）JP</option>
  <option value="SG">新加坡（Singapore）SG</option>
  <option value="KR">韩国（South Korea）KR</option>
  <option value="TH">泰国（Thailand）TH</option>
  <option value="MY">马来西亚（Malaysia）MY</option>
  <option value="ID">印尼（Indonesia）ID</option>
  <option value="VN">越南（Vietnam）VN</option>
  <option value="PH">菲律宾（Philippines）PH</option>
  <option value="US">美国（United States）US</option>
  <option value="CF">中非共和国（Central African Republic）CF</option>
  <option value="DK">丹麦（Denmark）DK</option>
  <option value="UZ">乌兹别克斯坦（Uzbekistan）UZ</option>
  <option value="UG">乌干达（Uganda）UG</option>
  <option value="UY">乌拉圭（Uruguay）UY</option>
  <option value="YE">也门（Yemen）YE</option>
  <option value="AP">亚洲 13 国（13 Asian Countries）AP</option>
  <option value="AM">亚美尼亚（Armenia）AM</option>
  <option value="IL">以色列（Israel）IL</option>
  <option value="BG">保加利亚（Bulgaria）BG</option>
  <option value="HR">克罗地亚（Croatia）HR</option>
  <option value="WW_109">全球 109 国（Global 109 Countries）WW_109</option>
  <option value="GU">关岛（Guam）GU</option>
  <option value="GU_MP">关岛/塞班（Guam/Saipan）GU_MP</option>
  <option value="IS">冰岛（Iceland）IS</option>
  <option value="LI">列支敦士登（Liechtenstein）LI</option>
  <option value="CD">刚果民主共和国（Democratic Republic of the Congo）CD</option>
  <option value="LR">利比里亚（Liberia）LR</option>
  <option value="CA">加拿大（Canada）CA</option>
  <option value="GH">加纳（Ghana）GH</option>
  <option value="HU">匈牙利（Hungary）HU</option>
  <option value="ZA">南非共和国（South Africa）ZA</option>
  <option value="BQ">博内尔（Bonaire）BQ</option>
  <option value="QA">卡塔尔（Qatar）QA</option>
  <option value="RW">卢旺达（Rwanda）RW</option>
  <option value="LU">卢森堡（Luxembourg）LU</option>
  <option value="IN">印度（India）IN</option>
  <option value="GT">危地马拉（Guatemala）GT</option>
  <option value="EC">厄瓜多尔（Ecuador）EC</option>
  <option value="KG">吉尔吉斯斯坦（Kyrgyzstan）KG</option>
  <option value="KZ">哈萨克斯坦（Kazakhstan）KZ</option>
  <option value="CO">哥伦比亚（Colombia）CO</option>
  <option value="CR">哥斯达黎加（Costa Rica）CR</option>
  <option value="CM">喀麦隆（Cameroon）CM</option>
  <option value="TR">土耳其（Turkey (Turkiye)）TR</option>
  <option value="LC">圣卢西亚（Saint Lucia）LC</option>
  <option value="KN">圣基茨和尼维斯（Saint Kitts and Nevis）KN</option>
  <option value="VC">圣文森特和格林纳丁斯（Saint Vincent and the Grenadines）VC</option>
  <option value="GY">圭亚那（Guyana）GY</option>
  <option value="TZ">坦桑尼亚（Tanzania）TZ</option>
  <option value="EG">埃及（Egypt）EG</option>
  <option value="TJ">塔吉克斯坦（Tajikistan）TJ</option>
  <option value="RS">塞尔维亚（Serbia）RS</option>
  <option value="SL">塞拉利昂（Sierra Leone）SL</option>
  <option value="CY">塞浦路斯（Cyprus）CY</option>
  <option value="MP">塞班（Saipan）MP</option>
  <option value="SC">塞舌尔（Seychelles）SC</option>
  <option value="MX">墨西哥（Mexico）MX</option>
  <option value="DM">多米尼克（Dominica）DM</option>
  <option value="DO">多米尼加共和国（Dominican Republic）DO</option>
  <option value="AT">奥地利（Austria）AT</option>
  <option value="BD">孟加拉（Bangladesh）BD</option>
  <option value="AI">安圭拉（Anguilla）AI</option>
  <option value="AG">安提瓜和巴布达（Antigua and Barbuda）AG</option>
  <option value="NI">尼加拉瓜（Nicaragua）NI</option>
  <option value="NP">尼泊尔（Nepal）NP</option>
  <option value="PK">巴基斯坦（Pakistan）PK</option>
  <option value="BB">巴巴多斯（Barbados）BB</option>
  <option value="PG">巴布亚新几内亚（Papua New Guinea）PG</option>
  <option value="PY">巴拉圭（Paraguay）PY</option>
  <option value="PA">巴拿马（Panama）PA</option>
  <option value="BR">巴西（Brazil）BR</option>
  <option value="GR">希腊（Greece）GR</option>
  <option value="KY">开曼群岛（Cayman Islands）KY</option>
  <option value="DE">德国（Germany）DE</option>
  <option value="IT">意大利（Italy）IT</option>
  <option value="LV">拉脱维亚（Latvia）LV</option>
  <option value="NO">挪威（Norway）NO</option>
  <option value="CZ">捷克共和国（Czech Republic）CZ</option>
  <option value="MD">摩尔多瓦（Moldova）MD</option>
  <option value="MA">摩洛哥（Morocco）MA</option>
  <option value="BN">文莱（Brunei）BN</option>
  <option value="FJ">斐济（Fiji）FJ</option>
  <option value="SZ">斯威士兰（Eswatini）SZ</option>
  <option value="SK">斯洛伐克（Slovakia）SK</option>
  <option value="SI">斯洛文尼亚（Slovenia）SI</option>
  <option value="LK">斯里兰卡（Sri Lanka）LK</option>
  <option value="NZ">新西兰（New Zealand）NZ</option>
  <option value="CL">智利（Chile）CL</option>
  <option value="KH">柬埔寨（Cambodia）KH</option>
  <option value="GD">格林纳达（Grenada）GD</option>
  <option value="GE">格鲁吉亚（Georgia）GE</option>
  <option value="EU_33">欧洲 33 国（33 European Countries）EU_33</option>
  <option value="BE">比利时（Belgium）BE</option>
  <option value="MU">毛里求斯（Mauritius）MU</option>
  <option value="TO">汤加（Tonga）TO</option>
  <option value="SA">沙特阿拉伯（Saudi Arabia）SA</option>
  <option value="FR">法国（France）FR</option>
  <option value="GF">法属圭亚那（French Guiana）GF</option>
  <option value="FO">法罗群岛（Faroe Islands）FO</option>
  <option value="PL">波兰（Poland）PL</option>
  <option value="BA">波斯尼亚和黑塞哥维那（Bosnia and Herzegovina）BA</option>
  <option value="HN">洪都拉斯（Honduras）HN</option>
  <option value="HT">海地（Haiti）HT</option>
  <option value="AU">澳大利亚（Australia）AU</option>
  <option value="MO">澳门（Macau）MO</option>
  <option value="IE">爱尔兰（Ireland）IE</option>
  <option value="EE">爱沙尼亚（Estonia）EE</option>
  <option value="JM">牙买加（Jamaica）JM</option>
  <option value="TC">特克斯和凯科斯群岛（Turks and Caicos Islands）TC</option>
  <option value="TT">特立尼达和多巴哥（Trinidad and Tobago）TT</option>
  <option value="SE">瑞典（Sweden）SE</option>
  <option value="CH">瑞士（Switzerland）CH</option>
  <option value="GP">瓜德罗普（Guadeloupe）GP</option>
  <option value="VU">瓦努阿图（Vanuatu）VU</option>
  <option value="BY">白俄罗斯（Belarus）BY</option>
  <option value="BM">百慕大（Bermuda）BM</option>
  <option value="GI">直布罗陀（Gibraltar）GI</option>
  <option value="PE">秘鲁（Peru）PE</option>
  <option value="TN">突尼斯（Tunisia）TN</option>
  <option value="LT">立陶宛（Lithuania）LT</option>
  <option value="JO">约旦（Jordan）JO</option>
  <option value="RO">罗马尼亚（Romania）RO</option>
  <option value="US_CA">美国/加拿大（United States/Canada）US_CA</option>
  <option value="LA">老挝（Laos）LA</option>
  <option value="KE">肯尼亚（Kenya）KE</option>
  <option value="FI">芬兰（Finland）FI</option>
  <option value="SD">苏丹（Sudan）SD</option>
  <option value="GB">英国（United Kingdom）GB</option>
  <option value="VG">英属维尔京群岛（British Virgin Islands）VG</option>
  <option value="NL">荷兰（Netherlands）NL</option>
  <option value="MZ">莫桑比克（Mozambique）MZ</option>
  <option value="SV">萨尔瓦多（El Salvador）SV</option>
  <option value="PT">葡萄牙（Portugal）PT</option>
  <option value="PT_ES">葡萄牙/西班牙（Portugal/Spain）PT_ES</option>
  <option value="MN">蒙古（Mongolia）MN</option>
  <option value="ES">西班牙（Spain）ES</option>
  <option value="CI">象牙海岸（Ivory Coast）CI</option>
  <option value="ZM">赞比亚（Zambia）ZM</option>
  <option value="AZ">阿塞拜疆（Azerbaijan）AZ</option>
  <option value="DZ">阿尔及利亚（Algeria）DZ</option>
  <option value="AL">阿尔巴尼亚（Albania）AL</option>
  <option value="OM">阿曼（Oman）OM</option>
  <option value="AR">阿根廷（Argentina）AR</option>
  <option value="AE">阿联酋（United Arab Emirates）AE</option>
  <option value="AW">阿鲁巴（Aruba）AW</option>
  <option value="KR_JP">韩国/日本（South Korea/Japan）KR_JP</option>
  <option value="HK_MO">香港/澳门（Hong Kong/Macau）HK_MO</option>
  <option value="MW">马拉维（Malawi）MW</option>
  <option value="MQ">马提尼克（Martinique）MQ</option>
  <option value="MT">马耳他（Malta）MT</option>
  <option value="MG">马达加斯加（Madagascar）MG</option>
  <option value="ME">黑山（Montenegro）ME</option>
</select>
<label>SKU / 套餐</label>
<select id="sku" name="sku"></select>
<label>币种</label>
<select id="currency" name="currency">
  <option value="THB">THB 泰铢</option>
  <option value="EUR">EUR 欧元</option>
  <option value="USD">USD 美元</option>
  <option value="GBP">GBP 英镑</option>
  <option value="KRW">KRW 韩元</option>
  <option value="JPY">JPY 日元</option>
  <option value="SGD">SGD 新币</option>
  <option value="CNY">CNY 人民币</option>
  <option value="IDR">IDR 印尼盾</option>
</select>
<label>参考币种 / 对比币种</label>
<select id="referenceCurrencySelect">
  <option value="AUTO">按目的地自动</option>
  <option value="CNY">CNY 人民币</option>
  <option value="HKD">HKD 港币</option>
  <option value="TWD">TWD 新台币</option>
  <option value="JPY">JPY 日元</option>
  <option value="KRW">KRW 韩元</option>
  <option value="THB">THB 泰铢</option>
  <option value="SGD">SGD 新币</option>
  <option value="AUD">AUD 澳元</option>
  <option value="GBP">GBP 英镑</option>
  <option value="USD">USD 美元</option>
  <option value="EUR">EUR 欧元</option>
  <option value="IDR">IDR 印尼盾</option>
</select>
<div class="muted">默认按目的地本地币种估算，比如 TW 显示 TWD；也可以手动改成 CNY 等。</div>
<input type="hidden" name="coupon" value="HAN000000">
<input type="hidden" name="affiliate_code" value="HAN000000">
<div class="summary" id="summary">正在加载官方 SKU...</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
<button id="btn" type="submit" disabled>创建自建付款页</button>
<button id="officialBtn" type="button" disabled style="background:#111827">直接去官方结算页</button>
</div>
<div id="status" class="muted"></div>
</form>
</div>
<script>
const country=document.getElementById('country'), skuSel=document.getElementById('sku'), currency=document.getElementById('currency'), referenceCurrencySelect=document.getElementById('referenceCurrencySelect'), summary=document.getElementById('summary'), btn=document.getElementById('btn'), officialBtn=document.getElementById('officialBtn'), status=document.getElementById('status');
let catalog=[];
let referenceCurrency='CNY';
function money(p,cur){return p&&p.prices&&p.prices[cur]?p.prices[cur].display:'--'}
function finalMoney(p,cur){return p&&p.discounted_prices&&p.discounted_prices[cur]?p.discounted_prices[cur].display:money(p,cur)}
function priceAmount(p,cur){const src=p&&p.discounted_prices&&p.discounted_prices[cur]?p.discounted_prices:p&&p.prices;return src&&src[cur]&&typeof src[cur].amount==='number'?src[cur].amount:null}
function cnyRate(cur){const rates={THB:0.21,GBP:9.15,AUD:4.70,SGD:5.55,USD:7.20,HKD:0.92,TWD:0.23,JPY:0.047,CNY:1,EUR:7.75,KRW:0.0052,IDR:0.00045};return rates[cur]||null}
function currencySymbol(cur){const symbols={THB:'฿',GBP:'£',AUD:'A$',SGD:'S$',USD:'$',HKD:'HK$',TWD:'NT$',JPY:'¥',CNY:'¥',EUR:'€',KRW:'₩',IDR:'Rp'};return symbols[cur]||cur+' '}
function currencyDecimals(cur){return ['JPY','KRW','IDR'].includes(cur)?0:2}
function formatRefMoney(v,cur){if(v==null)return '--';const d=currencyDecimals(cur);return '≈'+currencySymbol(cur)+(d===0?Math.round(v).toString():v.toFixed(d))}
function refAmount(p,cur,refCur){const amount=priceAmount(p,cur), from=cnyRate(cur), to=cnyRate(refCur); if(amount==null||!from||!to)return null; return amount*from/to;}
function selectedRefCurrency(p){return referenceCurrencySelect.value==='AUTO'?(p&&p.reference_currency||referenceCurrency||'CNY'):referenceCurrencySelect.value}
function priceCompareHtml(p){const refCur=selectedRefCurrency(p);const curList=['THB','EUR','USD','GBP','KRW','JPY','SGD','CNY','IDR'];const rows=curList.map(cur=>{const amount=priceAmount(p,cur), ref=refAmount(p,cur,refCur);return amount==null?null:{cur,display:finalMoney(p,cur),ref,discount:p.discounted_prices&&p.discounted_prices[cur]&&p.discounted_prices[cur].discountDisplay};}).filter(Boolean).sort((a,b)=>(a.ref??999999)-(b.ref??999999));const best=rows[0];const label=(p.discounted_prices&&Object.keys(p.discounted_prices).length)?`按最高折扣后统一 ${refCur} 估算对比`:`官方标价统一按 ${refCur} 估算对比`;return `<div class=muted style="margin-top:8px"><b>${label}：</b>${rows.map(r=>`<span class=pill ${best&&r.cur===best.cur?'style="background:#ecfdf5;border-color:#bbf7d0;color:#166534;font-weight:700"':''}>${r.cur} ${r.display} = ${formatRefMoney(r.ref,refCur)}${r.discount?'（减'+r.discount+'）':''}${best&&r.cur===best.cur?' 最低':''}</span>`).join('')}</div><div class=muted>参考币种默认按目的地自动切换，也可在上方手动改成 CNY/HKD/TWD/JPY/AUD 等：澳洲=AUD，中国大陆=CNY，香港/澳门=HKD，台湾=TWD，日本=JPY，韩国=KRW，泰国=THB，新加坡=SGD，英国=GBP，美国/加拿大=USD，欧洲=EUR。折扣币种：THB减฿175、EUR减€4、USD减$5、GBP减£4、KRW减₩6750、JPY减¥775、SGD减S$6.75、CNY减¥36.25、IDR减Rp80000。汇率为前端估算，最终以官方结算页为准。</div>`}
function bestCurrency(p){const refCur=selectedRefCurrency(p);const rows=Object.keys(p.discounted_prices&&Object.keys(p.discounted_prices).length?p.discounted_prices:p.prices||{}).map(cur=>({cur,ref:refAmount(p,cur,refCur)})).filter(x=>x.ref!=null).sort((a,b)=>a.ref-b.ref);return rows[0]?rows[0].cur:currency.value}
function officialUrl(p){const slug=p.country_slug||country.value.toLowerCase().replaceAll('_','-');const q=new URLSearchParams({duration:String(p.duration_days||5),utm_source:'affiliate',affiliate_code:'HAN000000',promo:'affiliate-influencer'});return `https://www.superalink.com/cn/esim/${slug}?${q.toString()}`}
function skuLabel(p){let opt=p.option==='UNLIMITED'?'无限':'固定'; let data=(p.data_amount||'')+(p.data_unit||''); return `${p.duration_days}天 / ${opt} / ${data} / ${p.sku}`;}
async function loadCatalog(){btn.disabled=true; officialBtn.disabled=true; skuSel.innerHTML='<option>加载中...</option>'; summary.textContent='正在读取官方 SKU...';
  try{let r=await fetch('/api/catalog?country_code='+encodeURIComponent(country.value)); let j=await r.json(); if(!j.ok) throw new Error(j.error||'catalog failed'); catalog=j.products||[]; referenceCurrency=j.reference_currency||'CNY'; skuSel.innerHTML='';
    for(const p of catalog){let o=document.createElement('option'); o.value=p.sku; o.textContent=skuLabel(p)+' · '+finalMoney(p,bestCurrency(p)); skuSel.appendChild(o)}
    let preferred=catalog.find(p=>p.sku==='CN-5GB_UNLIMITED-5GB-5-DAYS')||catalog.find(p=>p.option==='UNLIMITED'&&p.duration_days===5&&p.data_amount===5&&p.data_unit==='GB')||catalog[0];
    if(preferred){skuSel.value=preferred.sku; currency.value=bestCurrency(preferred);}
    updateSummary(); btn.disabled=!preferred; officialBtn.disabled=!preferred;
  }catch(e){summary.innerHTML='<span style="color:#c62828">加载 SKU 失败：'+e.message+'</span>'; skuSel.innerHTML='';}}
function updateSummary(){const p=catalog.find(x=>x.sku===skuSel.value); if(!p){summary.textContent='请选择 SKU'; btn.disabled=true; officialBtn.disabled=true; return;} skuSel.querySelectorAll('option').forEach(o=>{const pp=catalog.find(x=>x.sku===o.value); if(pp)o.textContent=skuLabel(pp)+' · '+finalMoney(pp,bestCurrency(pp))}); const best=bestCurrency(p); const url=officialUrl(p); const refCur=selectedRefCurrency(p); const refMode=referenceCurrencySelect.value==='AUTO'?'按目的地自动':'手动指定'; summary.innerHTML=`<div class=row><span>SKU</span><b><code>${p.sku}</code></b></div><div class=row><span>套餐</span><b>${skuLabel(p).split(' / '+p.sku)[0]}</b></div><div class=row><span>参考币种</span><b>${refCur}（${refMode}）</b></div><div class=row><span>系统推荐币种</span><b>${best} ${best===currency.value?'已选择':'（点击套餐后已自动优先选择）'}</b></div><div class=row><span>当前选择币种</span><b>${currency.value}</b></div><div class=row><span>${(p.discounted_prices&&p.discounted_prices[currency.value])?'预估折后金额':'官方标价'}</span><b>${finalMoney(p,currency.value)} <span class=muted>${formatRefMoney(refAmount(p,currency.value,refCur),refCur)}</span></b></div>${priceCompareHtml(p)}<div class=muted>优惠券 HAN000000 会在创建订单/官方页面时应用。可选择本站创建自建付款页，也可直接跳官方产品结算页。</div><div class=muted>官方直达：<a href="${url}" target="_blank" rel="noopener">${url}</a></div><div class=notice style="margin-top:10px"><b>官方页优惠提示：</b>如果直达官方页面后价格没有自动折扣，可在付款时手动填写优惠券 <b>HAN000000</b>；也可以先点一下官网首页弹窗领券：<a href="https://www.superalink.com/destination/aff/HAN000000" target="_blank" rel="noopener">https://www.superalink.com/destination/aff/HAN000000</a></div>`; officialBtn.onclick=()=>{window.open(url,'_blank','noopener')}; btn.disabled=false; officialBtn.disabled=false;}
country.addEventListener('change',loadCatalog); skuSel.addEventListener('change',()=>{const p=catalog.find(x=>x.sku===skuSel.value); if(p) currency.value=bestCurrency(p); updateSummary();}); currency.addEventListener('change',updateSummary); referenceCurrencySelect.addEventListener('change',()=>{const p=catalog.find(x=>x.sku===skuSel.value); if(p) currency.value=bestCurrency(p); updateSummary();}); loadCatalog();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, text, status=200, extra_headers=None):
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def proxy_superalink_asset(self, parsed):
        upstream_url = WWW + self.path
        resp = requests.get(upstream_url, headers={"User-Agent": self.headers.get("User-Agent", "Mozilla/5.0")}, timeout=30)
        body = resp.content
        ctype = resp.headers.get("Content-Type", "")
        # Patch JS chunks so browser API calls go to same-origin Caddy proxy. This avoids
        # CORS/third-party-cookie issues while keeping Superalink's native checkout UI.
        if "javascript" in ctype or parsed.path.endswith(".js"):
            text = body.decode("utf-8", "ignore")
            text = text.replace("https://storefront.api.superalink.com", LOCAL_API_BASE)
            text = text.replace('"https://storefront.api.superalink.com"', json.dumps(LOCAL_API_BASE))
            text = text.replace("http://storefront-service.dev.superalink.com", LOCAL_API_BASE)
            text = text.replace('"http://storefront-service.dev.superalink.com"', json.dumps(LOCAL_API_BASE))
            body = text.encode("utf-8")
        elif "text/html" in ctype:
            text = body.decode("utf-8", "ignore")
            text = text.replace("https://storefront.api.superalink.com", LOCAL_API_BASE)
            text = text.replace('"https://storefront.api.superalink.com"', json.dumps(LOCAL_API_BASE))
            text = text.replace("http://storefront-service.dev.superalink.com", LOCAL_API_BASE)
            text = text.replace('"http://storefront-service.dev.superalink.com"', json.dumps(LOCAL_API_BASE))
            body = text.encode("utf-8")
        self.send_response(resp.status_code)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/_next/"):
            self.proxy_superalink_asset(parsed)
            return
        if parsed.path == "/":
            self.send_html(INDEX_HTML)
            return
        if parsed.path.endswith("/api/create"):
            params = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
            self.handle_create(params)
            return
        if parsed.path == "/flow":
            params = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
            try:
                result = result_from_params(params)
                self.send_response(302)
                self.send_header("Location", result["pay_url"])
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return
        if parsed.path == "/.well-known/apple-developer-merchantid-domain-association":
            try:
                body = open(os.environ.get("APPLE_PAY_DOMAIN_ASSOCIATION_FILE", "./apple-developer-merchantid-domain-association"), "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Cache-Control", "public, max-age=3600")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return
        if parsed.path == "/api/stripe-callback":
            # Mirror the vendor callback endpoint used after Stripe confirmation.
            try:
                upstream = STORE + "/v2/callback" + (("?" + parsed.query) if parsed.query else "")
                r = requests.get(upstream, headers={"User-Agent": self.headers.get("User-Agent", "Mozilla/5.0"), "Accept-Language": DEFAULT_LOCALE}, timeout=30)
                body = """<!doctype html><meta charset='utf-8'><title>支付结果</title><style>body{font-family:system-ui;max-width:620px;margin:40px auto;padding:0 16px}.ok{color:#0a8a3a}.err{color:#c62828}</style><h2>支付已提交</h2><p>如果支付成功，Superalink 会把 eSIM 发到你填写的邮箱。</p><p><a href='/'>返回首页</a></p>"""
                self.send_html(body, 200 if r.status_code < 500 else 502, {"Cache-Control": "no-store"})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return
        if parsed.path == "/pay":
            try:
                params = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
                token = params.get("t", "")
                data = load_token(token)
                data["token"] = token
                data["native_url"] = f"/go?t={token}"
                self.send_html(pay_html(data), extra_headers={"Cache-Control": "no-store"})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return
        if parsed.path == "/bridge":
            try:
                params = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
                data = load_token(params.get("t", ""))
                sid = data["cookie_value"]
                target = params.get("target") or urlparse(data["checkout_url"]).path
                body = f"""<!doctype html><meta charset='utf-8'><title>Superalink 预填跳转</title>
<script>
try {{
  document.cookie = 'splnk_checkout_session=' + encodeURIComponent({json.dumps(sid)}) + '; path=/; max-age=86400; SameSite=Lax; Secure';
  document.cookie = "NEXT_LOCALE=cn; path=/; max-age=86400; SameSite=Lax; Secure";
  localStorage.setItem('CHECKOUT_SESSION', {json.dumps(sid)});
}} catch(e) {{}}
location.replace({json.dumps(target)});
</script>
<p>正在进入 Superalink 官方付款页...</p>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Set-Cookie", f"splnk_checkout_session={sid}; Path=/; Max-Age=86400; SameSite=Lax; Secure")
                self.send_header("Set-Cookie", "NEXT_LOCALE=cn; Path=/; Max-Age=86400; SameSite=Lax; Secure")
                self.send_header("Content-Length", str(len(body.encode())))
                self.end_headers()
                self.wfile.write(body.encode())
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return
        if parsed.path == "/go":
            try:
                params = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
                token = params.get("t", "")
                data = load_token(token)
                target_path = urlparse(data["checkout_url"]).path
                bridge_url = f"/bridge?t={token}&target={urlencode({'next': target_path})[5:]}"
                body = f"""<!doctype html><meta charset='utf-8'><title>跳转中</title>
<style>body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:680px;margin:40px auto;padding:0 16px}}.muted{{color:#666}}</style>
<h2>正在打开 Superalink 官方付款页...</h2>
<p>邮箱：{html.escape(data.get('email') or '')}</p>
<p>优惠券：{html.escape(data.get('coupon') or '')}</p>
<p>金额：{html.escape(data.get('amount') or '')}</p>
<p class=muted>如果没有自动跳转，请点击：<a href="{html.escape(bridge_url)}">打开官方付款页</a></p>
<script>location.replace({json.dumps(bridge_url)});</script>"""
                self.send_html(body)
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return
        if parsed.path == "/api/catalog":
            params = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
            try:
                cc = (params.get("country_code") or DEFAULT_COUNTRY_CODE).upper()
                self.send_json({"ok": True, "country_code": cc, "reference_currency": local_reference_currency(cc), "products": catalog_for_country(cc)}, 200)
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return
        if parsed.path == "/api/products":
            params = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
            r = requests.get(f"{STORE}/products", params={"country_code": params.get("country_code", DEFAULT_COUNTRY_CODE)}, headers={"Accept-Language": DEFAULT_LOCALE}, timeout=25)
            try:
                self.send_json(r.json(), r.status_code)
            except Exception:
                self.send_json({"raw": r.text}, r.status_code)
            return
        self.send_json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/paypal/create":
            n = int(self.headers.get("Content-Length", "0") or 0)
            body = self.rfile.read(n) if n else b"{}"
            try:
                params = json.loads(body.decode() or "{}")
                data = load_token(params.get("t", ""))
                order_id = data["order_id"]
                sid = data["cookie_value"]
                sess = requests.Session()
                headers = page_headers(DEFAULT_URL, DEFAULT_LOCALE)
                bind_session_cookies(sess, sid, DEFAULT_LOCALE)
                order = get_order(sess, headers, order_id)
                email = (params.get("email") or "").strip()
                order = update_recipient_email(sess, headers, order_id, email, subscribe=False) or order
                pi = make_paypal_intent(sess, headers, order_id)
                cap = authorize_capture(sess, headers, order_id, pi.get("id"))
                data["paypal_intent_id"] = pi.get("id")
                data["paypal_order_id"] = (pi.get("meta") or {}).get("orderId")
                self.send_json({"ok": True, "paypal_order_id": data["paypal_order_id"], "pre_capture": cap.get("ok")})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return
        if parsed.path == "/api/paypal/capture":
            n = int(self.headers.get("Content-Length", "0") or 0)
            body = self.rfile.read(n) if n else b"{}"
            try:
                params = json.loads(body.decode() or "{}")
                data = load_token(params.get("t", ""))
                order_id = data["order_id"]
                sid = data["cookie_value"]
                sess = requests.Session()
                headers = page_headers(DEFAULT_URL, DEFAULT_LOCALE)
                bind_session_cookies(sess, sid, DEFAULT_LOCALE)
                intent_id = data.get("paypal_intent_id")
                if not intent_id:
                    intents = get_intents(sess, headers, order_id)
                    match = next((i for i in intents if i.get("methodIdentifier") == "paypal" and (i.get("meta") or {}).get("orderId") == params.get("paypal_order_id")), None)
                    intent_id = match.get("id") if match else None
                if not intent_id:
                    raise RuntimeError("paypal intent not found")
                cap = capture_intent(sess, headers, order_id, intent_id)
                self.send_json({"ok": cap.get("ok"), "capture_error": None if cap.get("ok") else cap.get("error")})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return
        if parsed.path == "/api/prepay":
            n = int(self.headers.get("Content-Length", "0") or 0)
            body = self.rfile.read(n) if n else b"{}"
            try:
                params = json.loads(body.decode() or "{}")
                data = load_token(params.get("t", ""))
                order_id = data["order_id"]
                sid = data["cookie_value"]
                sess = requests.Session()
                headers = page_headers(DEFAULT_URL, DEFAULT_LOCALE)
                bind_session_cookies(sess, sid, DEFAULT_LOCALE)
                order = get_order(sess, headers, order_id)
                email = (params.get("email") or "").strip()
                order = update_recipient_email(sess, headers, order_id, email, subscribe=False) or order
                cap = authorize_capture(sess, headers, order_id, data.get("stripe_intent_id"))
                cb = callback_query("stripe", sid)
                self.send_json({"ok": True, "pre_capture": cap.get("ok"), "pre_capture_error": None if cap.get("ok") else cap.get("error"), "callback": cb, "amount": data.get("amount"), "coupon": data.get("coupon")})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return
        if not parsed.path.endswith("/api/create"):
            self.send_json({"ok": False, "error": "not found"}, 404)
            return
        n = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(n) if n else b"{}"
        try:
            params = json.loads(body.decode() or "{}")
        except Exception:
            params = {}
        params.update({k: v[-1] for k, v in parse_qs(parsed.query).items()})
        self.handle_create(params)

    def handle_create(self, params):
        try:
            self.send_json(result_from_params(params), 200)
        except Exception as e:
            self.send_json({"ok": False, "error": str(e)}, 500)

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args), flush=True)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", 53333), Handler)
    print("Superalink checkout prefill listening on http://0.0.0.0:53333", flush=True)
    server.serve_forever()
