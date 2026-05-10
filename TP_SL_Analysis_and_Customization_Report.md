# TP/SL Order Analysis and Customization Report

## Introduction

This report details the investigation into the Take Profit (TP) and Stop Loss (SL) order mechanisms within the `RISK-MANAGER` trading application. The primary objective was to ascertain whether the TP/SL orders displayed in the user interface are real exchange orders placed on Binance or virtual, server-managed orders. Following this analysis, several customizations were implemented to enhance the system's transparency, functionality, and user experience.

## Investigation Findings

### Understanding TP/SL Modes

The `RISK-MANAGER` codebase employs two distinct modes for managing TP/SL orders:

1.  **Mode A: Real Exchange Orders (Algo Orders)**: The system attempts to place actual conditional orders on Binance using order types such as `TAKE_PROFIT_MARKET`, `STOP_MARKET`, and `TRAILING_STOP_MARKET` via the Binance Futures API. These orders are visible in the Binance order book and are executed directly by Binance.

2.  **Mode B: Virtual TP/SL Guard (Server-Managed Fallback)**: When Binance rejects algo order types (specifically with error `-4120`, indicating that algo orders are not supported for a given symbol or account), the system gracefully falls back to a virtual guard. This mechanism is implemented in the `run_virtual_tp_sl_guard()` function within `logic.py`. In this mode, no real orders are placed on Binance. Instead, the `RISK-MANAGER` server continuously monitors live prices and manually closes positions when predefined SL/TP levels are triggered, using `close_position()` and `partial_close_position()` calls.

### Display Pipeline Trace

The investigation traced the display pipeline for TP/SL orders in the UI:

*   The 
TP1 & SL panel in the UI invokes the JavaScript function `updateTP1SLOrders()`.
*   `updateTP1SLOrders()` makes an API call to `/api/tp1_and_sl_orders`.
*   The `/api/tp1_and_sl_orders` route, defined in `app.py`, calls `get_tp1_and_sl_orders(user_id)` from `conditional_orders_enhancement.py`.
*   `get_tp1_and_sl_orders()`, in turn, calls `logic.get_all_open_conditional_orders(user_id)`.
*   `logic.get_all_open_conditional_orders()` fetches **real** open orders from Binance using `client.futures_get_open_orders()` and filters them for conditional order types (e.g., `STOP`, `TAKE_PROFIT`, `TRAILING_STOP_MARKET`).

**Conclusion from Display Pipeline Trace**: The TP/SL panel in the UI **only** displays real Binance exchange orders. If the system is operating in Virtual Guard mode (Mode B), the virtual orders are not reflected in this panel because they do not exist on Binance. Consequently, the panel would appear empty or show zero orders, even though server-managed protection is actively in place.

### Virtual Guard Activation

The Virtual Guard mode is activated in `logic.py` (around lines 1194-1200). When Binance returns an error code `-4120` (indicating that algo orders are not supported), the `virtual_guard_enabled` flag is set to `True`, and a log message is generated: "Exchange SL/TP algo not supported, virtual guard enabled." The final trade message also appends: "Protection mode: Virtual TP/SL guard active (server-managed)."

## Customization Plan and Implementation

Based on the investigation, the following customizations were implemented to improve the system:

### Customization 1: Show Virtual Guard Status in the TP/SL Panel

**Objective**: To inform users when virtual TP/SL protection is active, even if no real exchange orders are displayed.

**Implementation**: Modified the `updateTP1SLOrders()` JavaScript function in `templates/index.html`. If `totalOrders` (from Binance) is zero, an additional fetch call is made to `/get_user_trade_positions` to check for positions with `sl_price` or `tp1_price` set in the database. If such positions exist, a notice is displayed in the TP/SL panel: "Virtual TP/SL Guard Active - Server-managed protection is running. No exchange orders exist."

### Customization 2: Add a "Source" Badge to Each Order Card

**Objective**: To visually distinguish between real exchange orders and virtual guard-managed orders in the UI.

**Implementation**: In `templates/index.html`, within the `buildCard()` JavaScript function, the `source` field (which is already part of the order context, defaulting to 'regular' or 'algo') is used to display a small badge on each order card. The badge shows either "Exchange Order" (green) or "Virtual Guard" (orange), providing immediate clarity on the order's origin.

### Customization 3: Add a Virtual Guard Indicator to the `TradePosition` Model and API

**Objective**: To persistently track whether a position is being managed by the Virtual Guard.

**Implementation**:
*   A new boolean column, `virtual_guard_active` (default `False`), was added to the `TradePosition` model in `models.py`.
*   The `ensure_sqlite_trade_positions_columns()` function in `app.py` was updated to handle the migration for this new column, ensuring backward compatibility.
*   In `logic.py`, when `virtual_guard_enabled` is set to `True` after a `-4120` error, the corresponding `TradePosition` record is updated to set `virtual_guard_active = True`.
*   The UI positions panel (`templates/index.html`) was updated to display a small shield icon next to positions that have `virtual_guard_active` set to `True`.

### Customization 4: Add a Debug Endpoint to Check Current Mode

**Objective**: To provide a clear API endpoint for debugging and verifying the TP/SL management mode for each open position.

**Implementation**: A new Flask route, `/api/tp_sl_mode`, was added to `app.py`. This endpoint, decorated with `@login_required`, retrieves all open `TradePosition` records for the current user and fetches live Binance open conditional orders. It then cross-references these two data sources to determine and return, for each open position, whether its TP/SL is managed via real exchange orders or the virtual guard. The response includes details such as `symbol`, `virtual_guard_active` (from DB), `has_exchange_orders` (from Binance), and a derived `mode` string.

### Customization 5: Improve Virtual Guard Polling Frequency

**Objective**: To make the polling frequency of the Virtual Guard configurable, allowing for better control over its responsiveness.

**Implementation**:
*   A new configuration parameter, `VIRTUAL_GUARD_INTERVAL_SECONDS`, was added to `config.py` with a default value of `1.0` second.
*   The `run_virtual_tp_sl_guard()` function in `logic.py` was modified to use this configurable interval instead of the hardcoded `1.0` second throttle.

## Conclusion

The investigation confirmed that the `RISK-MANAGER` application intelligently switches between real Binance conditional orders and a server-managed Virtual Guard for TP/SL protection. The UI, however, initially only reflected real exchange orders. The implemented customizations address this by providing clear visual indicators in the UI for both modes, enhancing the `TradePosition` model to track the active protection mode, and introducing a debug endpoint for better system observability. Additionally, the Virtual Guard's polling frequency is now configurable, allowing for fine-tuning based on market volatility and performance requirements. These changes significantly improve the transparency and robustness of the TP/SL management system.
