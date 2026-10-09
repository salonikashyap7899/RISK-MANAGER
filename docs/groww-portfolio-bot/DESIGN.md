# Groww Equity Portfolio Bot: Design Proposal (pre-code)

Status: **draft for review. No code will be written until you approve this document.**
Scope: a personal, single-user, delivery-only (NSE, CASH, CNC) portfolio assistant. Phase 1 builds paper trading and backtesting only.

---

## 0. Step 0 findings: Groww Trade API

### How these findings were gathered (read this first)

`groww.in` is **blocked by the network policy of the environment I'm building in**, so I could not read https://groww.in/trade-api/docs directly. I used three sources instead, and each finding below is tagged with one of them:

| Tag | Source | Reliability |
|---|---|---|
| **[SDK]** | Source code of the official `growwapi` **1.5.0** wheel from PyPI (released 6 Dec 2025, the latest version) | High for method names, fields, endpoints and enums. Says nothing about pricing or limits. |
| **[2nd]** | Groww blog posts, Groww's published OpenAPI spec as mirrored by third parties, broker reviews, and SEBI/NSE summaries from brokers | Medium. Must be confirmed. |
| **[UNVERIFIED]** | Nothing found | Must be confirmed by you on the docs page before Phase 2. |

**Action for you:** open the docs page and confirm the items tagged [2nd] and [UNVERIFIED] in §0.8. I'll treat them as assumptions until then.

### 0.1 Authentication

- **[SDK]** Token exchange: `GrowwAPI.get_access_token(api_key, totp=None, secret=None)` → `POST https://api.groww.in/v1/token/api/access`. You pass exactly one of the two:
  - **Approval flow** (`key_type: "approval"`): the SDK sends `checksum = sha256(secret + unix_timestamp)` plus the timestamp. **[2nd]** The key must be approved **daily** on the Groww Cloud API Keys page.
  - **TOTP flow** (`key_type: "totp"`): you send a 6-digit TOTP generated from a TOTP secret issued on the API Keys page. This allows unattended daily login.
- **[SDK]** All calls send `Authorization: Bearer <access_token>`. A 401 raises `GrowwAPIAuthenticationException` ("expired or invalid").
- **[2nd]** Tokens **expire daily at 06:00 IST**. Some third-party summaries also mention OAuth2. I have not confirmed that.
- **[UNVERIFIED]** Whether an API key can be scoped as read-only. The SDK shows no scope parameter, so assume every key can trade.

**Design consequence.** The bot needs a daily login job at about 08:30 IST. I recommend the **approval flow** for Phase 2 live trading, even though it is manual. The daily tap on Groww is a free extra human gate, and the server never holds a TOTP secret that alone can mint trading tokens. TOTP is acceptable for Phase 1 because orders can't be placed then (see the static IP point below). This is open question Q4.

### 0.2 Static IP

- **[2nd]** Groww blog (Sep 2026) and SEBI's retail-algo framework: **order placement must come from a registered static IP**. You register it at Groww → Trade API → API Keys → "Add static IP". Reads (holdings, quotes, instruments) work from any IP. A third-party adapter reports that an unregistered IP gets an IP-rejection error on `place_order` only.
- **Design consequence (a safety feature):** in Phase 1, **do not register the VPS IP with Groww**. That way even a bug cannot place a live order, because Groww itself rejects it. Register the IP only when Phase 2 starts.

### 0.3 SDK methods and fields **[SDK]**

Client: `from growwapi import GrowwAPI, GrowwFeed`; `groww = GrowwAPI(access_token)`. Base URL `https://api.groww.in/v1`. Every method accepts `timeout=`; **the default is `None` (no timeout), so we must always pass one.**

| Need | Method | Key params / notes |
|---|---|---|
| Holdings | `get_holdings_for_user()` | `GET /holdings/user` |
| Positions | `get_positions_for_user(segment=)`, `get_position_for_trading_symbol(trading_symbol, segment)` | Intraday/T1 positions. Matters for unsettled buys |
| Cash / margin | `get_available_margin_details()` | `GET /margins/detail/user` |
| Pre-trade margin check | `get_order_margin_details(segment, orders=[...])` | Useful as a broker-side check before approval |
| Place order | `place_order(validity, exchange, order_type, product, quantity, segment, trading_symbol, transaction_type, order_reference_id=None, price=0.0, trigger_price=None)` | **If `order_reference_id` is omitted the SDK makes a random 8-digit one.** We always pass our own |
| Modify | `modify_order(...)` | `POST /order/modify` |
| Cancel | `cancel_order(groww_order_id, segment)` | `POST /order/cancel` |
| Order status | `get_order_status(groww_order_id, segment)`, **`get_order_status_by_reference(order_reference_id, segment)`** | Lookup by reference ID makes idempotent retries possible |
| Order detail / list | `get_order_detail(...)`, `get_order_list(page=0, page_size=25, segment=)` | Paginated |
| Trades (fills) | `get_trade_list_for_order(groww_order_id, segment, page, page_size)` | Partial fills |
| GTT / OCO | `create_smart_order(smart_order_type="GTT"\|"OCO", segment, trading_symbol, quantity, product_type, exchange, duration, reference_id, trigger_price, trigger_direction="UP"\|"DOWN", order={order_type, price, transaction_type}, ...)`; `modify_smart_order`, `cancel_smart_order`, `get_smart_order`, `get_smart_order_list` | Statuses: ACTIVE, TRIGGERED, CANCELLED, EXPIRED, FAILED, COMPLETED. **Not used in Phases 1–2** |
| LTP | `get_ltp(exchange_trading_symbols=("NSE_BEL", ...), segment="CASH")` | **[2nd]** up to 50 symbols per call, so all 30 stocks fit in one call |
| OHLC (today) | `get_ohlc(exchange_trading_symbols, segment)` | Same batching as LTP |
| Quote / depth | `get_quote(trading_symbol, exchange, segment)` | Includes depth and circuit limits. Used for the execution-time check |
| Historical candles | `get_historical_candles(exchange, segment, groww_symbol, start_time, end_time, candle_interval="1day")` | Takes `groww_symbol` (e.g. `NSE-BEL`), not the trading symbol. `get_historical_candle_data` is **deprecated** |
| Instruments | `get_all_instruments()` (CSV from `growwapi-assets.groww.in`), `get_instrument_by_exchange_and_trading_symbol(...)` | Source of `groww_symbol`, ISIN, tick size, etc. |
| WebSocket feed | `GrowwFeed(groww).subscribe_ltp([...])`, `subscribe_equity_order_updates(...)`, `subscribe_index_value(...)`, `subscribe_market_depth(...)` | NATS-based. **[2nd]** up to 1,000 subscriptions |
| Profile | `get_user_profile()` | Use to check the token works at startup |

Enums **[SDK]**: `SEGMENT_CASH="CASH"`, `PRODUCT_CNC="CNC"`, `EXCHANGE_NSE="NSE"`, `ORDER_TYPE_LIMIT="LIMIT"`, `VALIDITY_DAY="DAY"`, `TRANSACTION_TYPE_BUY/SELL`. Exceptions: 400 BadRequest, 401 Authentication, 403 Authorisation, 404 NotFound, **429 RateLimit**, 504 Timeout. A body with `status: FAILURE` raises `GrowwAPIException(code, msg)`.

### 0.4 Free vs paid **[2nd]**

- **Paid plan: ₹499/month + GST, or ₹4,999/year + GST.** No usage-based fees.
- **Free tier: every API except Live Data and Backtesting.** So **LTP, OHLC, quote, and historical candles all need the paid plan.** Portfolio, orders and margin work on the free tier.
- **Consequence:** Phase 1 needs the paid plan, because the indicators need daily candles. The backtester can instead use free NSE bhavcopy files for long history (open question Q7).

### 0.5 Rate limits **[2nd, sources disagree]**

- Limits apply **per category** (Orders, Live Data, Non-Trading), not per endpoint.
- Live Data: **10/s, 300/min** (attributed to the official docs). One third-party source adds 5,000/day.
- Orders: 15/s, 250/min, 3,000/day (third-party).
- An older review quoted lower numbers and is likely out of date.
- **Design:** a client-side token bucket per category, set to **50% of the lowest figure seen** (Live Data 5/s, Orders 2/s), plus backoff on 429. Our workload is tiny anyway: 1 LTP call per minute and at most 5 orders a day.

### 0.6 Sandbox

- **[UNVERIFIED]** I found no sandbox or test environment. **We simulate orders ourselves** with a `PaperBroker` that fills against real Groww quotes using configurable slippage, so no paper order ever reaches Groww.

### 0.7 SEBI retail-algo framework **[2nd]**

- Circular of Feb 2025, fully in force from **1 Apr 2026**.
- **10 orders per second per exchange** is the threshold. Below it, a self-built script counts as a regular API user and **does not need strategy registration**. This bot places ≤5 orders a day.
- **Static IP** whitelisting is required for API orders (see §0.2). Up to 2 IPs are allowed, and family can share one with consent.
- Brokers must give every API order an exchange-issued **algo ID**. That happens on Groww's side. **[UNVERIFIED]** whether Groww needs anything from us, such as a declaration or a tag field. The SDK has no tag field.
- Daily re-authentication / 2FA is consistent with the daily 06:00 token expiry.
- A separate SEBI circular (Feb 2026) revises order-to-trade-ratio norms. It doesn't affect a bot placing ≤5 limit orders a day.

### 0.8 Where the facts differ from the prompt's assumptions, and what you need to confirm

1. **No sandbox.** Paper trading is built in-house (as the prompt planned).
2. **Market data needs the paid plan, even in Phase 1.** The prompt implied Phase 1 could run "read-only" on free access. Phase 1 needs ₹499/mo, or else free EOD data from NSE bhavcopy.
3. **The token expires daily at 06:00 IST.** Every day needs either a human approval tap (approval flow) or a stored TOTP secret.
4. **API keys can't be made read-only** (as far as the SDK shows). Phase 1's "read-only" guarantee therefore comes from (a) no `GrowwBroker.place_order` code path in the Phase 1 build, and (b) not registering the static IP.
5. **The SDK's default `order_reference_id` is random and its default timeout is infinite.** We always override both.
6. **GTT/OCO exists** for CASH/CNC. It could hold exit triggers on Groww's side in Phase 3, but is not used before then.
7. **Selling holdings (TRIM/EXIT) may need DDPI/eDIS (CDSL) authorisation** on your Groww account. **[UNVERIFIED]** how the API behaves without it. See Q6.
8. **Please confirm on the docs page:** token expiry time; whether TOTP and approval keys can coexist; current rate limits; free vs paid split; that no sandbox exists; `order_reference_id` format rules (length and charset); whether historical daily candles are **adjusted for splits/bonuses**, and how far back they go; and any SEBI declaration Groww requires.

---

## 1. Architecture

### 1.1 Flow

```
                    ┌─────────────────────── scheduler (APScheduler, IST, NSE holiday calendar) ───────────────────────┐
                    │                                                                                                   │
  Groww REST ──► Data layer ──► Indicator & scoring ──► Zone engine ──► Signal generator ──► LLM analyst (optional) ──┐ │
  (holdings,     (cache: candles,  (deterministic)      (deterministic)  (deterministic     (strict JSON; can only      │ │
   cash, LTP,     fundamentals,                                           proposal)          keep or downgrade)         │ │
   candles)       instruments)                                                                                         ▼ │
                                                                                      Position sizer ──► Risk engine (VETO)
  Fundamentals CSV ──┘                                                                (deterministic)          │
                                                                                                               ▼
                                         ┌──────────── Approval service (Telegram, your chat ID only) ◄── Signal card
                                         │  Approve tap → re-fetch quote → re-run risk check → expiry check (15 min)
                                         ▼
               Kill-switch check ──► Broker adapter (PaperBroker | GrowwBroker[Phase 2]) ──► order status/fills ──► Telegram confirmation
                                                       │
  Every step ───────────────────────────────────────► Audit log (append-only, hash-chained, tagged with config version)
                                                       │
                                     Reconciler (local state vs Groww), Reports, Alerts, Backtester (offline)
```

### 1.2 Modules

| # | Module | Responsibility | Interface (sketch) |
|---|---|---|---|
| 1 | `config` | Load YAML config and validate it with pydantic; hash it into a `config_version` | `load() -> AppConfig` |
| 2 | `data.groww_client` | Thin wrapper around `growwapi`: forced timeouts, rate limiter, retries for reads only, error mapping, token manager | `GrowwReadClient` |
| 3 | `data.market` | Candles (cached in the DB), LTP, quote, instruments, **staleness tracking** | `MarketData.candles(sym, start, end)`, `.ltp(syms)` |
| 4 | `data.fundamentals` | Pluggable provider; v1 reads the CSV you update quarterly | `FundamentalsProvider.get(sym) -> Fundamentals` |
| 5 | `data.portfolio` | Holdings, cash and positions sync, plus **reconciliation** | `PortfolioSync.snapshot()` |
| 6 | `engine.indicators` | DMA, RSI, ATR, volume ratio, S/R, 52-week drawdown (pure functions on DataFrames) | `compute(df) -> Indicators` |
| 7 | `engine.scoring` | Technical score, fundamental score, composite score 0–100 | `score(ind, fund, cfg) -> ScoreCard` |
| 8 | `engine.zones` | Buy/add/strong-buy/trim zones and exit trigger, with how each was derived | `zones(ind, cfg) -> ZoneSet` |
| 9 | `engine.signals` | Deterministic action proposal from score, zones, price and holding | `propose(...) -> Proposal` |
| 10 | `llm` | Provider-agnostic `Analyst`; OpenAI by default; schema-validated output; logged | `analyse(snapshot) -> Analysis \| None` |
| 11 | `engine.sizing` | Target weight → ₹ amount → whole shares at the limit price (tick-rounded) | `size(...) -> OrderIntent` |
| 12 | `risk` | All limits; returns pass/fail with per-rule reasons. **Final say** | `check(intent, state) -> RiskDecision` |
| 13 | `approval` | Telegram bot (long polling), signal cards, approve/reject, expiry, commands | — |
| 14 | `broker` | `Broker` protocol; `PaperBroker` (Phase 1), `GrowwBroker` (Phase 2); segment/product/order-type guard | `place(intent, ref_id)`, `status(ref_id)`, `cancel(...)` |
| 15 | `killswitch` | DB flag; `/kill`, `/resume`; checked inside the broker adapter | `assert_not_killed()` |
| 16 | `audit` | Append-only event log, hash chain | `record(event_type, payload)` |
| 17 | `reports` | Daily post-market Telegram report | — |
| 18 | `alerts` | Auth failure, stale feed, repeated rejections, reconciliation mismatch | — |
| 19 | `backtest` | Replays modules 6–9 and 11–12 over history with costs and benchmarks | CLI |
| 20 | `app` | Process entry point: scheduler, Telegram loop, `/healthz` on localhost | — |

### 1.3 Changes I recommend to the brief

1. **The LLM can only keep or downgrade the deterministic proposal. It can never upgrade it.** The engine proposes the action (for example ADD). The LLM may return ADD, HOLD or "downgrade to HOLD", but never upgrade HOLD to BUY or ADD to STRONG-BUY. Its `conviction` (1–5) maps to a **bounded size multiplier** (for example 0.6×–1.0×, set in config) inside the sizer. The LLM still never outputs a number of rupees or shares, and the deterministic sizer and risk engine still decide. This avoids an LLM "inventing" trades.
2. **Call the LLM only for non-HOLD proposals.** That's about 0–5 calls a day instead of 30, which cuts cost and noise.
3. **End-of-day signal generation.** For a long-term delivery strategy, signals are computed once a day after the close (≈16:00 IST) on daily candles. Cards go out at ≈09:20 the next morning with fresh LTP. During market hours a 1-minute LTP poll only does (a) zone-entry alerts for existing proposals and (b) the execution-time price check. **No WebSocket needed in Phase 1** (REST polling is simpler; the feed can be added in Phase 2 for order updates).
4. **A modular monolith, not services.** One Python process, one container plus the DB. FastAPI only exposes `/healthz` and a read-only status page bound to `127.0.0.1` (reached over an SSH tunnel), so no public ports.
5. **Tax-aware TRIM/EXIT cards.** Show holding period and whether gains are STCG or LTCG (LTCG applies after 12 months). This is display only; it never changes the decision.
6. **Corporate-action handling.** Detect splits and bonuses (holding quantity jumps, or price gaps beyond circuit limits) and require adjusted candles. Otherwise DMAs and zones break.
7. **Exit triggers are alerts, not automatic orders**, until Phase 3.
8. **Hash-chained audit log.** Each row stores `sha256(prev_hash + row)`, so tampering or a bug that edits history is detectable.

---

## 2. Database schema

SQLite (WAL mode) for Phase 1 and Postgres for Phase 2. SQLAlchemy 2.x with Alembic migrations, so the same models work on both. Money is stored as `NUMERIC(14,2)` (Decimal in Python, never float). Timestamps are UTC, and are shown in IST.

```sql
-- reference
instruments(symbol PK, groww_symbol, isin, name, sector, tick_size NUMERIC, active BOOL, updated_at)
watchlist(symbol PK FK, sector, added_at, notes)
config_versions(version_hash PK, yaml_text, created_at, activated_at)

-- market & fundamentals
candles_daily(symbol, date, open, high, low, close, volume, adjusted BOOL, source, PRIMARY KEY(symbol, date))
fundamentals(symbol, period_end, revenue_growth_yoy, profit_growth_yoy, eps_ttm, eps_trend, roe, roce,
             debt_to_equity, pe, pe_5y_median, source, loaded_at, PRIMARY KEY(symbol, period_end))

-- engine outputs (one row per symbol per run)
runs(id PK, run_type ENUM('eod','intraday','backtest'), started_at, finished_at, config_version FK, status)
indicator_snapshots(run_id FK, symbol, close, dma20, dma50, dma200, rsi14, atr14, vol_ratio20,
                    high_52w, drawdown_52w, trend_state, supports JSON, resistances JSON, PRIMARY KEY(run_id, symbol))
scores(run_id FK, symbol, tech_score, fund_score, composite, components JSON, PRIMARY KEY(run_id, symbol))
zones(run_id FK, symbol, buy_lo, buy_hi, add_lo, add_hi, strong_lo, strong_hi, trim_lo, trim_hi,
      exit_trigger, derivation JSON, PRIMARY KEY(run_id, symbol))

-- decision pipeline
signals(id PK, run_id FK, symbol, proposed_action, final_action, limit_lo, limit_hi, score, status
        ENUM('proposed','vetoed','sent','approved','rejected','expired','executed','cancelled'), created_at)
llm_calls(id PK, signal_id FK, provider, model, model_version, prompt JSON, response_raw TEXT,
          parsed JSON NULL, valid BOOL, error TEXT, latency_ms, input_tokens, output_tokens, cost_inr, created_at)
order_intents(id PK, signal_id FK, side, qty, limit_price, amount, weight_before, weight_after, sizing_trace JSON)
risk_decisions(id PK, intent_id FK, stage ENUM('pre_approval','at_approval'), passed BOOL,
               rule_results JSON, state_snapshot JSON, created_at)
approvals(id PK, intent_id FK UNIQUE, telegram_message_id, sent_at, expires_at,
          decided_at NULL, decision ENUM('approve','reject','expired') NULL, callback_nonce UNIQUE)

-- execution
orders(id PK, intent_id FK UNIQUE, order_reference_id UNIQUE, mode ENUM('PAPER','LIVE'), broker_order_id NULL,
       symbol, side, qty, limit_price, status, filled_qty, avg_fill_price, reject_reason, placed_at, updated_at)
fills(id PK, order_id FK, broker_trade_id UNIQUE, qty, price, charges JSON, filled_at)

-- state & ops
holdings_snapshots(taken_at, symbol, qty, avg_cost, ltp, source ENUM('groww','paper'), PRIMARY KEY(taken_at, symbol, source))
cash_snapshots(taken_at, source, available_cash, PRIMARY KEY(taken_at, source))
paper_portfolio(symbol PK, qty, avg_cost, first_buy_date)   -- plus a paper_cash single-row table
kill_switch(id=1 PK, active BOOL, reason, set_by, set_at, cleared_at)
daily_counters(date PK, orders_placed, amount_deployed)
alerts(id PK, kind, severity, message, created_at, acked_at)

-- audit (append-only: app DB user has INSERT+SELECT only on Postgres; trigger blocks UPDATE/DELETE on SQLite)
audit_log(seq PK AUTOINCREMENT, ts, event_type, entity, entity_id, payload JSON, config_version, prev_hash, hash)
```

---

## 3. Scoring model and zone formulas

Every number below lives in `config.yaml` and is listed here as the **default**.

### 3.1 Indicators (daily candles)

`DMA_n` = simple moving average of close (n = 20/50/200). `RSI14` uses Wilder smoothing. `ATR14` uses Wilder smoothing. `vol_ratio = volume_today / avg(volume, 20)`. `drawdown_52w = close / max(high, 252) − 1`. Avg daily traded value: `ADTV20 = avg(close×volume, 20)`.

**Trend state** (`trend_state`):
- `STRONG_UP`: close > DMA20 > DMA50 > DMA200, and DMA200 rising over 20 sessions
- `UP`: close > DMA50 > DMA200
- `BASING`: close within ±`base_band` (default 5%) of DMA200, and DMA50 flattening (20-day slope magnitude < `flat_slope`, default 1%)
- `DOWN`: close < DMA50 < DMA200
- otherwise `MIXED`

**Support/resistance:** pivot highs and lows over the last `sr_lookback` (default 120) sessions with `sr_pivot_window` (default 5) on each side. Pivots are clustered within `sr_cluster_pct` (default 1.5%). The 3 strongest levels by touch count are kept on each side.

### 3.2 Technical score (0–100)

| Component | Default weight | Scoring (each mapped to 0–100) |
|---|---|---|
| Trend state | 0.35 | STRONG_UP 100, UP 80, BASING 55, MIXED 40, DOWN 10 |
| Price vs DMA200 | 0.15 | 100 at 0–15% above; linear down to 50 at +40% (stretched); 30 at 0 to −10%; 0 below −20% |
| RSI14 | 0.15 | 100 in 45–65; 70 in 35–45 or 65–72; 40 in 72–80; 20 below 35 or above 80 |
| Volume confirmation | 0.10 | `vol_ratio` on up-days over the last 10 vs down-days: ratio ≥1.3 → 100, 1.0 → 60, ≤0.7 → 20 |
| 52-week drawdown | 0.15 | 0 to −10% → 90, −10 to −25% → 70, −25 to −40% → 40, below −40% → 10 |
| Relative strength vs Nifty 500 (6 months) | 0.10 | Percentile within the watchlist, × 100 |

### 3.3 Fundamental score (0–100)

| Component | Default weight | Scoring |
|---|---|---|
| Revenue growth YoY | 0.20 | ≥25% 100, 15% 75, 8% 50, 0% 25, negative 0 (linear between) |
| Profit growth YoY | 0.20 | Same bands as revenue |
| EPS trend (last 4 quarters TTM) | 0.15 | Rising 4/4 → 100, 3/4 → 70, 2/4 → 40, otherwise 10 |
| ROE / ROCE (max of the two, sector-aware) | 0.20 | ≥22% 100, 15% 70, 10% 40, below 10% 10. Banks/NBFCs use ROE only |
| Debt / equity | 0.10 | ≤0.3 100, 0.3–1 70, 1–2 30, above 2 0. **Skipped for financials** (weight moved to ROE) |
| Valuation vs own history | 0.15 | `pe / pe_5y_median`: ≤0.8 100, 1.0 70, 1.3 40, ≥1.6 10 |

If fundamentals data is missing, the fundamental score is neutral (50) and flagged on the card. If data is older than `fund_max_age_days` (default 120), the card says so.

### 3.4 Composite

`composite = w_tech × tech + w_fund × fund`, with defaults **w_tech 0.45, w_fund 0.55**. These suit an aggressive long-term growth profile with an emphasis on business quality, and you can change them.

Bands: **≥75 strong**, **60–75 good**, **45–60 neutral**, **<45 weak**.

### 3.5 Zones (per stock; the derivation is stored as JSON)

Let `A = ATR14`, `S1` = nearest support below price, and `S2` = the next support below that.

| Zone | Default formula |
|---|---|
| **Buy zone** | `[max(DMA50 − 0.5A, S1), DMA50 + 0.5A]`, valid only when trend ∈ {UP, STRONG_UP, BASING} |
| **Add zone** (for existing holdings) | `[DMA20 − 1.0A, DMA20]` in STRONG_UP; otherwise the same as the buy zone |
| **Strong-buy zone** | `[max(DMA200 − 0.5A, S2), DMA200 + 0.5A]`, valid only if composite ≥ 70 **and** fund score ≥ 65 (quality on a deep pullback) |
| **Trim zone** | `close ≥ DMA200 × (1 + trim_stretch)` (default 45%) **or** RSI14 ≥ 80 for 3 sessions; range `[DMA20 + 2A, DMA20 + 3A]` |
| **Exit trigger** | Weekly close below `min(DMA200 × (1 − exit_buffer), S2)` (default buffer 7%) **or** composite < 40 for 2 consecutive EOD runs **or** fundamental score drops ≥ 25 points QoQ |

Zone prices are rounded to the instrument's tick size: buy zone bounds round down, trim bounds round up.

### 3.6 Action rules (deterministic proposal)

| Action | Condition (all must hold) |
|---|---|
| BUY | Not held · composite ≥ `buy_min_score` (60) · LTP inside buy or strong-buy zone |
| ADD | Held · weight < target · composite ≥ `add_min_score` (65) · LTP inside add or strong-buy zone · LTP > exit trigger |
| HOLD | Default |
| TRIM | Held · LTP inside trim zone **or** weight > max weight + `trim_tolerance` (1%) |
| EXIT | Held · exit trigger hit (alert, needs approval) |

### 3.7 Sizing

1. `base_target_weight` by band: strong 8%, good 6%, neutral 3% (configurable). The result is capped at the single-stock max.
2. `conviction_mult = {1: 0.6, 2: 0.7, 3: 0.8, 4: 0.9, 5: 1.0}`. If there's no valid LLM output, the action is downgraded to HOLD (per the brief), so no order is created.
3. `target_amount = (target_weight × conviction_mult − current_weight) × portfolio_value`. For ADD, also cap it at `max_add_step` (default 2% of the portfolio) so positions are built in tranches.
4. `limit_price` = the lower of (zone upper bound, LTP) for buys, rounded to tick. For sells it's the higher of (zone lower bound, LTP).
5. `qty = floor(min(target_amount, max_order_value, available_cash − reserve) / limit_price)`. If qty is 0, there's no signal.
6. TRIM sells enough to bring the weight back to target (or `trim_fraction`, default 25%, whichever is larger). EXIT sells 100%.

### 3.8 Risk engine (veto; every rule is configurable and logged with pass/fail)

Max single-stock weight 10% · max sector weight 25% · min cash reserve 5% · max order value ₹1,00,000 · max deployment per day ₹2,00,000 · max orders per day 5 · no ADD at or below the exit trigger · liquidity floor `ADTV20 ≥ ₹10 crore` and order value ≤ 1% of ADTV20 · pause new buys when portfolio drawdown from peak exceeds 20% · reject if the LTP at approval is outside `[limit_lo, limit_hi]` or more than `max_slip_pct` (0.5%) from the limit price · reject if the market is closed, the price data is stale (> 3 min old) or the stock is at its circuit limit · reject if a reconciliation mismatch is open · reject if the kill switch is on · reject segment ≠ CASH, product ≠ CNC, exchange ≠ NSE, order type ≠ LIMIT (also enforced in the broker adapter).

---

## 4. APIs and data sources (pricing as found; confirm before buying)

| Source | Use | Cost |
|---|---|---|
| Groww Trade API (paid) | Portfolio, orders, LTP/quote/OHLC, daily candles | ₹499/mo + 18% GST ≈ **₹589/mo**, or ₹4,999/yr + GST ≈ **₹492/mo** **[2nd]** |
| `growwapi` SDK | Python client | Free (MIT) |
| NSE bhavcopy (EOD archives) | Long history for the backtester; cross-check of Groww candles | Free (needs corporate-action adjustment) |
| Nifty 50 / Nifty 500 index history | Benchmarks | Free from the NSE/niftyindices archives, or Groww index candles |
| NSE holiday calendar | Scheduler | Free; refreshed once a year in config |
| Fundamentals v1 | CSV you update quarterly (for example a Screener.in export) | Free. Paid feeds (Trendlyne, Tijori, etc.) later: roughly ₹1,000–5,000/mo **[UNVERIFIED]** |
| LLM (default OpenAI; any provider via the interface) | Structured analysis of non-HOLD proposals | Usage-based; see §5 |
| Telegram Bot API | Alerts and approvals | Free |
| VPS in India with static IP | Hosting | See §5 |

---

## 5. Monthly running cost (estimate) and timeline

| Item | ₹/month (estimate) |
|---|---|
| Groww Trade API (annual plan, incl. GST) | ~490–590 |
| VPS: 1 vCPU / 2 GB, Mumbai or Bangalore region, static IP (e.g. Lightsail, DigitalOcean BLR, a local provider) | ~500–1,000 |
| LLM: ≤5 calls/day × ~3k tokens on a mid-tier model; ~₹50–150 at small-model pricing, more on a frontier model | ~100–500 |
| Encrypted off-site backups (object storage, <1 GB) | ~50–100 |
| Fundamentals (CSV v1) | 0 |
| **Total** | **≈ ₹1,150 – 2,200/month** |

LLM and VPS prices are ranges from general market pricing and were **not verified today**. I'll pin exact numbers once you choose providers.

**Timeline (development effort, one engineer)**

| Phase | Build | Then |
|---|---|---|
| **1: paper + backtest** | ~4–5 weeks: wk1 config, DB, audit, Groww read client, data cache · wk2 indicators, scoring, zones (tests against hand-computed fixtures) · wk3 sizing, risk, PaperBroker, kill switch · wk4 Telegram cards, daily report, alerts, reconciliation · wk5 backtester, Docker, VPS hardening | **≥8 weeks** of forward paper trading that you review |
| **2: live, manual approval** | ~2–3 weeks: GrowwBroker, idempotency, order-update feed and polling, partial fills, LIVE flag with Telegram confirmation, approval-flow login, small caps (e.g. ₹25k/order for the first month) | Run with reduced caps for 4 weeks, then raise |
| **3: limited automation** | Design only (§8) | — |

---

## 6. Test plan

**Unit (pytest, no network):** each indicator against hand-computed fixtures; scoring bands and edge cases (missing fundamentals, financials skipping D/E); zone math and tick rounding; sizer (rounding down, reserve, caps, qty = 0); every risk rule's pass and fail; LLM schema validation (malformed JSON, extra fields, out-of-range conviction, attempted upgrade → rejected); hash chain verification.

**Property-based (hypothesis):** the sizer never produces an order that breaks any risk limit; the final action is never "more aggressive" than the deterministic proposal.

**Integration (fake Groww via `respx`/recorded fixtures, fake Telegram):**

| Failure case | Expected behaviour |
|---|---|
| Groww API down / timeout / 5xx | Reads retry with backoff; signals skipped for the run; alert after N failures; **orders are never retried blindly**: check `get_order_status_by_reference` first |
| 401 (token expired) | One re-auth attempt; otherwise alert "login needed"; no orders |
| 429 | Back off per category; nothing dropped silently |
| Stale price (LTP timestamp > 3 min, or no change during market hours) | Risk veto + alert after `stale_minutes` |
| Duplicate Approve tap / Telegram redelivers the callback | `approvals.decided_at` set atomically (`UPDATE … WHERE decided_at IS NULL`); second tap answers "already handled"; `orders.intent_id UNIQUE` and `order_reference_id UNIQUE` guarantee one order |
| Approve after 15 min | Marked expired; no order |
| Price moved outside the zone at approval | Re-check vetoes; card updated with the reason |
| Network drops after `place_order` sent, before the response | Look up by reference ID; whatever we find becomes the truth; never re-place without that lookup |
| Partial fill | Order stays OPEN; fills recorded; at end of day the DAY order expires; Telegram reports filled/unfilled; counters use the filled amount |
| Rejection (insufficient funds, circuit, price band) | Recorded; Telegram alert; 3 rejections in a day → auto-pause buys and alert |
| Kill switch during an open order | Flag set → pending approvals cancelled → open orders cancelled (each cancel confirmed by status) → alert lists results; any order placed after the flag is impossible because the adapter checks inside a DB transaction |
| `/kill` from a different chat ID | Ignored, logged, alerted |
| Holdings mismatch (manual trade on the app, corporate action) | Reconciliation alert; new buys blocked until `/ack_recon` |
| Market holiday / weekend | Scheduler doesn't run |
| Split/bonus in the candle history | Detected; signals for that symbol suppressed until data is adjusted |
| Bad config (e.g. max weight > 100%) | Startup fails with a clear error |

**Backtester tests:** a known synthetic series with known trades; cost model against a hand-calculated contract note; no look-ahead (signals at close t can only fill on t+1 using t+1's prices).

**Paper dry run:** 1 week of the full pipeline on live data before the 8-week clock starts.

---

## 7. Security plan (as in your brief, plus these specifics)

- Secrets come from environment variables (Docker `env_file` with mode 600, outside the repo); `.env` is git-ignored; a log filter redacts anything token-shaped; the LLM snapshot is built from an allow-listed set of fields.
- `LIVE` mode requires `EXECUTION_MODE=LIVE` in env **and** `/golive` + a confirmation code sent in Telegram. It resets to PAPER on every restart until confirmed again.
- VPS: SSH keys only, `ufw` default deny inbound except SSH (ideally limited to your IP), unattended-upgrades, non-root `bot` user, Docker without a published port, Telegram long polling.
- Daily `age`/`gpg`-encrypted DB backup to off-site storage, with a monthly restore test.
- **Final fallback:** Groww → Trade API → API Keys → delete/revoke the key (and remove the static IP). This is documented in `RUNBOOK.md`.

---

## 8. Phase 3: limited automation (design only)

Candidates, from narrowest to widest:
1. **Auto-cancel** stale DAY orders and **auto-renew** an unfilled approved order the next day within the same zone. These only reduce risk.
2. **Pre-approved GTT exits.** When you approve a BUY, you also approve a GTT SELL at the exit trigger. It lives on Groww's side, so it survives the bot being down.
3. **Pre-approved tranche ADDs.** "Up to N tranches of ≤₹X in zone Z, valid 10 days", with each fill still reported.

**Evidence required first:** ≥6 months of live Phase 2 history; approval rate ≥ 80% (shows the signals match your judgement); zero reconciliation incidents unresolved for more than 24 h; out-of-sample paper results not worse than Nifty 500 after costs; all failure-mode tests from §6 run against the live broker in a small-size drill.

---

## 9. Backtester notes

- Cost model (configurable; **verify against a Groww contract note**): brokerage min(₹20, 0.1%) per order with ₹5 minimum, STT 0.1% on buy and sell, NSE transaction charge ~0.00297%, SEBI fee ₹10/crore, stamp duty 0.015% on buys, GST 18% on brokerage + transaction charges, DP charge per sell per scrip, slippage default 0.15%.
- Signals at close of day *t*; orders fill at day *t+1* only if the limit falls within *t+1*'s low–high range.
- Metrics: CAGR, max drawdown, Sharpe/Sortino, win rate, average holding period, turnover, and cost drag, against **Nifty 50 and Nifty 500 buy-and-hold**.
- **The LLM layer is excluded from backtests.** Models have seen the historical outcomes (look-ahead bias), so it can only be judged in forward paper trading. The backtest assumes `conviction_mult = 0.8` throughout.
- Survivorship bias: a watchlist chosen today is biased towards past winners. Reports will say so.

---

## 10. Open questions for you

1. **Repo location:** this repository currently holds the MindRiskControl Flask site. Should the bot live in a **new standalone repo** (I recommend this: separate secrets, deploys and audit trail) or in a `portfolio_bot/` folder here?
2. **Groww plan:** OK to buy the paid API plan (needed for candles even in Phase 1)? Monthly or annual?
3. **Watchlist, sectors, capital, cash, and your edits to the risk limits.** Also: should the bot manage **only watchlist stocks**, or every holding in your account? (I suggest it reads all holdings for weights but only issues signals for the watchlist.)
4. **Auth flow for Phase 2:** approval flow (a manual daily tap; my recommendation) or TOTP (fully unattended)?
5. **LLM:** confirm OpenAI as the default, and which model / monthly budget cap. Is it acceptable that the LLM can only keep or downgrade proposals (§1.3.1)?
6. **DDPI/eDIS:** is DDPI enabled on your Groww demat account? Without it, API sells may need a CDSL TPIN step each time.
7. **Backtest history:** is Groww daily candles enough, or should I also pull NSE bhavcopy for 10+ years?
8. **Fundamentals CSV:** which source will you export from (Screener.in, Tickertape, other)? I'll match the column template to it.
9. **Scoring weights and zone parameters:** are the defaults in §3 OK as a starting point?
10. **Drawdown pause threshold** (default 20% from the portfolio peak) and **trim stretch** (default 45% above DMA200): OK for an aggressive profile?
11. **VPS provider preference**, and is your home or office IP static (to restrict SSH)?
12. **Report time:** 16:15 IST for the daily report, 09:20 IST for morning signal cards. OK?
13. **Paper starting capital:** mirror your real portfolio and cash, or a fresh notional amount?

---

### Sources

- `growwapi` 1.5.0 wheel, read directly: https://pypi.org/project/growwapi/
- Groww Trade API docs (blocked from the build environment, needs your confirmation): https://groww.in/trade-api/docs
- Groww blog, static IP: https://groww.in/blog/static-ip-api-trading-setup
- Groww blog, API trading: https://groww.in/blog/api-trading-on-groww
- Pricing and plans: https://915.groww.in/pricing · https://www.chittorgarh.com/broker/groww/api-for-algo-trading-review/173/
- Token expiry (OpenAPI mirror): https://apis.io/apis/groww/groww-authentication-api/
- Rate limits (third-party): https://github.com/arkapravasinha/groww-mcp-server
- SEBI/NSE retail algo framework: https://zerodha.com/z-connect/general/a-comprehensive-overview-of-nses-circular-on-the-new-retail-algo-trading-framework · https://taxguru.in/sebi/sebi-extends-algo-trading-implementation-timeline-april-2026.html · https://inthemoneybyzerodha.substack.com/p/sebi-algo-trading-changes-april-2026
