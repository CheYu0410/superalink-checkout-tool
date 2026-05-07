# Superalink Checkout Tool

一个自托管的 Superalink eSIM 下单/付款辅助工具，用来在自己的域名上提供“小白模式”的购买入口。

当前项目默认服务于：

- 公网入口：`https://supera.onlypast.com/`
- 本地监听端口：`53333`
- 默认优惠码/邀请码：`HAN000000`
- 默认推荐套餐：中国大陆 eSIM，5 天，无限流量 / 每天 5GB，THB 支付约 `฿25.00`

> 说明：本项目不是破解、绕过或替代 Superalink 官方支付风控；它的核心思路是每次点击都新建官方 checkout 订单，并在自建页面中尽量复刻官方 checkout 的选择与支付流程。

## 功能特性

- 站内选择目的地、SKU/套餐、币种，不需要用户复制粘贴官方链接。
- 实时从 Superalink storefront API 拉取产品 SKU。
- 每次点击自动创建新的 Superalink checkout 订单，避免复用一次性 checkout 链接。
- 默认自动应用优惠码/邀请码 `HAN000000`。
- 使用服务端内存短 token 保存 checkout/session/payment 数据，前端 URL 不直接暴露敏感支付数据。
- 自建付款页支持：
  - 邮箱输入；
  - Stripe Payment Element：银行卡等常规支付方式；
  - Stripe Express Checkout：Apple Pay / Google Pay / Link，是否显示取决于设备、浏览器、钱包和 Stripe 商户域名状态；
  - PayPal SDK Buttons；
  - PayPal 降级按钮和状态提示。
- 保留旧版官方 checkout 代理回退路径 `/go`、`/bridge`，但主流程是自建付款页 `/pay`。
- 提供 Apple Pay merchant domain association 文件路由。

## 目录结构

```text
.
├── superalink_checkout_tool.py                 # 主服务，Python 标准库 http.server + requests
├── apple-developer-merchantid-domain-association # Apple Pay 域名关联文件
├── Caddyfile.example                           # Caddy 反代示例
├── .env.example                                # 环境变量示例
├── .gitignore
└── README.md
```

## 环境要求

- Python 3.11+
- Python 包：`requests`
- 反代建议：Caddy
- 端口：`53333`

当前代码没有依赖 Flask，不需要额外 Web 框架。

## 快速启动

```bash
cd /root/superalink-checkout-tool

# 设置支付前端配置。不要把真实值提交到 Git。
export STRIPE_PK='pk_live_xxx'
export PAYPAL_CLIENT_ID='xxx'

# 可选：Apple Pay 域名关联文件路径
export APPLE_PAY_DOMAIN_ASSOCIATION_FILE='./apple-developer-merchantid-domain-association'

python3 superalink_checkout_tool.py
```

启动后会监听：

```text
http://0.0.0.0:53333
```

如果要后台运行，可以用 systemd、supervisor、tmux，或当前环境的进程管理工具。

## Caddy 反代示例

参考仓库里的 `Caddyfile.example`。

核心要求：

```caddy
supera.onlypast.com {
    reverse_proxy 127.0.0.1:53333
}
```

完整示例额外包含：

- `/storefront-api/*` 反代到 `storefront.api.superalink.com`；
- `/_next/*`、`/checkout/*` 等旧代理 checkout 回退路径；
- Apple Pay 域名关联文件路径。

修改 Caddy 配置后建议执行：

```bash
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
```

## 主要访问路径

### 首页

```text
GET /
```

显示站内选择器：目的地、SKU/套餐、币种、优惠券/邀请码。

### 拉取 SKU catalog

```text
GET /api/catalog?country_code=CN
```

从 Superalink 官方 storefront API 拉取当前目的地可售 SKU。

### 创建订单并进入付款页

```text
GET /flow?country_code=CN&sku=CN-5GB_UNLIMITED-5GB-5-DAYS&currency=THB&coupon=HAN000000&affiliate_code=HAN000000
```

流程：

1. 创建 Superalink checkout 订单；
2. 获取或创建 payment intent；
3. 把 checkout session、client secret、order id 等敏感数据保存在服务端内存；
4. 返回 `302 /pay?t=...`。

### 自建付款页

```text
GET /pay?t=短token
```

页面中可输入邮箱，然后选择 Stripe / Express Checkout / PayPal 支付。

### Stripe 预处理

```text
POST /api/prepay
```

只更新 recipient/email 字段，不再修改 currency/SKU/qty/coupon，避免官方接口报错：

```text
Cannot change currency after payment intent is created
```

### PayPal 创建订单

```text
POST /api/paypal/create
```

更新邮箱后创建 PayPal payment intent，并返回 PayPal Buttons 需要的 order id。

### PayPal capture

```text
POST /api/paypal/capture
```

PayPal approve 后执行 capture/authorize-capture。

### Apple Pay 域名关联文件

```text
GET /.well-known/apple-developer-merchantid-domain-association
```

用于 Apple Pay merchant domain verification。

## 默认套餐参数

默认推荐中国大陆 eSIM：

```text
country_code=CN
sku=CN-5GB_UNLIMITED-5GB-5-DAYS
currency=THB
coupon=HAN000000
affiliate_code=HAN000000
```

目标价格：

```text
小计：฿200.00
优惠：-฿175.00
合计：฿25.00
```

实际价格以 Superalink 官方接口返回为准。

## 支付方式说明

### 银行卡 / 常规支付

通过 Stripe Payment Element 显示。

### Apple Pay / Google Pay / Link

通过 Stripe Express Checkout 和 Payment Element wallets 尽量启用。

是否出现取决于：

- 用户设备和浏览器，例如 Apple Pay 通常需要 Safari；
- 用户是否已添加钱包；
- Stripe 商户账户是否启用对应钱包；
- Apple Pay 是否已验证当前域名；
- Stripe 对当前支付环境的判断。

### PayPal

通过 PayPal SDK 动态加载官方 Buttons。

如果 SDK 加载失败，页面会显示明确状态，并提供降级按钮。

## 安全说明

请不要提交或公开以下内容：

- checkout order id；
- payment intent id；
- Stripe `client_secret`；
- Stripe secret key；
- PayPal order id；
- PayPal client id 的生产值；
- checkout session cookie；
- 任意用户邮箱、手机号、订单数据；
- `.env` 文件。

当前仓库只保留 `.env.example`，真实配置应通过环境变量注入。

## 已知限制

1. 官方 checkout 链接不能长期复用  
   `/checkout/superalink-...` 是一次性/会话型链接，依赖 cookie/session。复制到新浏览器或新设备可能无法加载。

2. 不建议代理官方 checkout 页面作为主流程  
   官方页面包含 Cloudflare Turnstile 等风控组件，在自定义域名代理下可能无法稳定通过。

3. Apple Pay 不能仅靠代码强制显示  
   即使代码和 `.well-known` 文件都准备好，也需要 Stripe/Apple Pay 商户侧允许并验证当前域名。

4. Token 为内存存储  
   `/pay?t=...` 里的 token 保存在 Python 进程内存中，默认约 30 分钟过期。服务重启后旧 token 会失效。

## 开发检查

语法检查：

```bash
python3 -m py_compile superalink_checkout_tool.py
```

检查端口监听：

```bash
ss -ltnp 'sport = :53333'
```

查看 Git 状态：

```bash
git status --short
git log --oneline -5
```

## 免责声明

本项目用于自托管购买入口和流程自动化研究。实际支付、订单、退款、履约、风控和可用性均以 Superalink 官方系统和支付服务商为准。请遵守相关平台服务条款，不要用于绕过安全验证、风控或未授权访问。
