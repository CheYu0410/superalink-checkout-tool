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
        amount, unit = product_data_amount(p)
        out.append({
            "sku": p.get("sku"),
            "kind": p.get("kind"),
            "option": p.get("dataPlan", {}).get("option"),
            "duration_days": duration_days(p),
            "data_amount": amount,
            "data_unit": unit,
            "prices": p.get("price", {}),
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
<style>body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#f6f7f9;margin:0;color:#111}}.wrap{{max-width:560px;margin:18px auto;padding:16px}}.card{{background:#fff;border-radius:16px;box-shadow:0 8px 28px rgba(0,0,0,.08);padding:20px;margin-bottom:14px}}h2{{margin:0 0 8px}}label{{display:block;font-weight:650;margin:14px 0 6px}}input{{width:100%;box-sizing:border-box;padding:13px;border:1px solid #ddd;border-radius:10px;font-size:16px}}button{{width:100%;padding:15px;border:0;border-radius:10px;background:#0a7cff;color:white;font-size:17px;margin-top:16px}}button:disabled{{opacity:.55}}.muted{{color:#666;font-size:14px;line-height:1.55}}.row{{display:flex;justify-content:space-between;margin:8px 0}}.ok{{color:#0a8a3a}}.err{{color:#c62828;white-space:pre-wrap}}#payment-element{{margin-top:12px}}#paypal-status{{margin:10px 0 8px}}.paypal-fallback{{background:#ffc439;color:#111;font-weight:700}}</style></head>
<body><div class=wrap>
<div class=card><h2>{title}</h2><p class=muted>{fup_text} · SKU {sku}</p>
<div class=row><span>优惠券</span><b>{safe_coupon}</b></div><div class=row><span>应付</span><b>{safe_amount}</b></div><div id=status class="muted">正在加载银行卡支付组件...</div></div>
<div class=card><form id=pay-form><label>接收 eSIM 的邮箱</label><input id=email type=email autocomplete=email placeholder="you@example.com" required><div id="wallet-status" class="muted">正在检测 Apple Pay / Google Pay / Link...</div><div id="express-element"></div><div id="paypal-status" class="muted">正在加载 PayPal...</div><div id="paypal-buttons"></div><button id="paypal-fallback" type="button" class="paypal-fallback">PayPal 支付 {safe_amount}</button><div id="payment-element"></div><button id=submit disabled>银行卡 / 钱包支付 {safe_amount}</button><div id=message class=err></div></form><p class=muted>已尽量启用：银行卡、Apple Pay、Google Pay、Link、PayPal。Apple/Google Pay 是否显示取决于用户设备、浏览器和钱包环境。</p><p class=muted>如果自建支付失败，可点这里回退到原生代理页：<a id="native-link" href="#">打开原生页</a></p></div>
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
body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#f6f7f9;margin:0;padding:18px;color:#111}.wrap{max-width:680px;margin:18px auto;background:white;border-radius:16px;box-shadow:0 8px 30px rgba(0,0,0,.08);padding:22px}h2{margin-top:0}label{display:block;margin:14px 0 6px;font-weight:650}select,input{width:100%;box-sizing:border-box;padding:13px;border:1px solid #ddd;border-radius:10px;font-size:16px;background:#fff}button{margin-top:18px;width:100%;padding:14px 18px;border:0;border-radius:10px;background:#0a7cff;color:white;font-size:17px;cursor:pointer}button:disabled{opacity:.55;cursor:not-allowed}.muted{color:#666;font-size:14px;line-height:1.55}.pill{display:inline-block;background:#eef5ff;border:1px solid #cfe4ff;padding:4px 8px;border-radius:999px;font-size:13px;margin:3px 4px 3px 0}.summary{background:#f8fafc;border:1px solid #e5e7eb;border-radius:12px;padding:12px;margin-top:14px}.notice{background:#fff7ed;border:1px solid #fed7aa;border-radius:12px;padding:12px 14px;margin:14px 0;color:#7c2d12;font-size:13px;line-height:1.6}.notice b{color:#9a3412}.row{display:flex;justify-content:space-between;gap:12px;margin:6px 0}code{font-size:12px;word-break:break-all}
</style></head><body>
<div class="wrap">
<h2>Superalink 自建付款页</h2>
<p class="muted">实现站内自选：目的地、SKU/套餐、币种都在本页选择，不需要跳去官网或粘链接。默认优惠券 <b>HAN000000</b>。</p>
<div class="notice"><b>免责声明：</b>本站只是 Superalink eSIM 的自助下单辅助入口，商品、价格、支付、订单履约、售后与退款均以 Superalink 官方及支付服务商实际结果为准。请在付款前自行核对套餐、目的地、天数、流量、币种和最终金额；本站不保证所有支付方式在所有设备/浏览器中都可用，也不提供绕过风控或安全验证的服务。</div>
<form id="orderForm" method="GET" action="/flow" onsubmit="btn.disabled=true;status.textContent='正在按所选 SKU / 币种创建订单...';">
<label>目的地</label>
<select id="country" name="country_code">
  <option value="CN">中国大陆 CN</option>
  <option value="TW">台湾 TW</option>
  <option value="HK">香港 HK</option>
  <option value="JP">日本 JP</option>
  <option value="SG">新加坡 SG</option>
  <option value="KR">韩国 KR</option>
  <option value="TH">泰国 TH</option>
  <option value="MY">马来西亚 MY</option>
  <option value="ID">印尼 ID</option>
  <option value="VN">越南 VN</option>
  <option value="PH">菲律宾 PH</option>
  <option value="US">美国 US</option>
</select>
<label>SKU / 套餐</label>
<select id="sku" name="sku"></select>
<label>币种</label>
<select id="currency" name="currency">
  <option value="THB">THB 泰铢</option>
  <option value="CNY">CNY 人民币</option>
  <option value="HKD">HKD 港币</option>
  <option value="SGD">SGD 新币</option>
  <option value="USD">USD 美元</option>
  <option value="GBP">GBP 英镑</option>
  <option value="JPY">JPY 日元</option>
</select>
<input type="hidden" name="coupon" value="HAN000000">
<input type="hidden" name="affiliate_code" value="HAN000000">
<div class="summary" id="summary">正在加载官方 SKU...</div>
<button id="btn" type="submit" disabled>创建自建付款页</button>
<div id="status" class="muted"></div>
</form>
</div>
<script>
const country=document.getElementById('country'), skuSel=document.getElementById('sku'), currency=document.getElementById('currency'), summary=document.getElementById('summary'), btn=document.getElementById('btn'), status=document.getElementById('status');
let catalog=[];
function money(p,cur){return p&&p.prices&&p.prices[cur]?p.prices[cur].display:'--'}
function skuLabel(p){let opt=p.option==='UNLIMITED'?'无限':'固定'; let data=(p.data_amount||'')+(p.data_unit||''); return `${p.duration_days}天 / ${opt} / ${data} / ${p.sku}`;}
async function loadCatalog(){btn.disabled=true; skuSel.innerHTML='<option>加载中...</option>'; summary.textContent='正在读取官方 SKU...';
  try{let r=await fetch('/api/catalog?country_code='+encodeURIComponent(country.value)); let j=await r.json(); if(!j.ok) throw new Error(j.error||'catalog failed'); catalog=j.products||[]; skuSel.innerHTML='';
    for(const p of catalog){let o=document.createElement('option'); o.value=p.sku; o.textContent=skuLabel(p)+' · '+money(p,currency.value); skuSel.appendChild(o)}
    let preferred=catalog.find(p=>p.sku==='CN-5GB_UNLIMITED-5GB-5-DAYS')||catalog.find(p=>p.option==='UNLIMITED'&&p.duration_days===5&&p.data_amount===5&&p.data_unit==='GB')||catalog[0];
    if(preferred) skuSel.value=preferred.sku; updateSummary(); btn.disabled=!preferred;
  }catch(e){summary.innerHTML='<span style="color:#c62828">加载 SKU 失败：'+e.message+'</span>'; skuSel.innerHTML='';}}
function updateSummary(){const p=catalog.find(x=>x.sku===skuSel.value); if(!p){summary.textContent='请选择 SKU'; return;} skuSel.querySelectorAll('option').forEach(o=>{const pp=catalog.find(x=>x.sku===o.value); if(pp)o.textContent=skuLabel(pp)+' · '+money(pp,currency.value)}); summary.innerHTML=`<div class=row><span>SKU</span><b><code>${p.sku}</code></b></div><div class=row><span>套餐</span><b>${skuLabel(p).split(' / '+p.sku)[0]}</b></div><div class=row><span>币种</span><b>${currency.value}</b></div><div class=row><span>官方标价</span><b>${money(p,currency.value)}</b></div><div class=muted>优惠券 HAN000000 会在创建订单时应用，最终应付以付款页显示为准。</div>`;}
country.addEventListener('change',loadCatalog); skuSel.addEventListener('change',updateSummary); currency.addEventListener('change',updateSummary); loadCatalog();
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
                self.send_json({"ok": True, "country_code": cc, "products": catalog_for_country(cc)}, 200)
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
