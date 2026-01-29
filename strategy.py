"""
DC Weather Trading Strategy - Hedged YES (Multi-Market)

Strategy:
1. Scan ALL temperature markets (high and low, all cities)
2. Find bucket containing forecast (or closest to it)
3. Find adjacent bucket (above or below)
4. Only enter if BOTH buckets are ≤30¢ each (≤60¢ total)
5. Buy YES on both buckets
6. Hold to settlement - win 100¢ if temp lands in either

Guaranteed profit: Pay ≤60¢ total, win 100¢ = at least 40¢ profit
"""

import time
import json
from datetime import datetime, timedelta
from config import (
    MAX_BUCKET_PRICE, MAX_TOTAL_COST,
    FORECAST_CHECK_INTERVAL, MARKETS
)
from nws_forecast import get_today_forecast, get_tomorrow_forecast
from orders import buy, sell, get_market_price, get_market_info, get_temperature_positions, cancel_order
from monitor import load_positions, save_positions, add_pending_order, reconcile_pending_orders
import requests


def parse_ticker_strikes(ticker):
    """
    Parse floor/cap strikes from ticker name.

    Examples:
        KXHIGHTDC-26JAN22-T49 -> floor=None, cap=49 (49 or below)
        KXHIGHTDC-26JAN22-B49.5 -> floor=49, cap=50 (49-50 bucket)
        KXHIGHTDC-26JAN22-T56 -> floor=56, cap=None (57 or above)
    """
    parts = ticker.split("-")
    if len(parts) < 3:
        return None, None

    strike_part = parts[-1]  # e.g., "T49", "B49.5", "T56"

    if strike_part.startswith("T"):
        strike = float(strike_part[1:])
        if strike <= 50:
            return None, int(strike)
        else:
            return int(strike), None
    elif strike_part.startswith("B"):
        strike = float(strike_part[1:])
        floor = int(strike)
        cap = floor + 1
        return floor, cap

    return None, None


def get_market_data_for_series(series_ticker, days_ahead=1):
    """Fetch current market data for a specific series."""
    target_date = datetime.now() + timedelta(days=days_ahead)
    date_str = target_date.strftime("%y%b%d").upper()
    event_ticker = f"{series_ticker}-{date_str}"

    url = f"https://api.elections.kalshi.com/trade-api/v2/events/{event_ticker}"
    params = {"with_nested_markets": True}

    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            return None, []
        data = resp.json()
        event = data.get("event", {})
        markets = event.get("markets", [])
        return event, markets
    except Exception as e:
        return None, []


def find_hedge_opportunity(markets, forecast_temp):
    """
    Find 2 adjacent buckets that BRACKET the forecast for hedged YES strategy.

    Strategy: Prefer one bucket below forecast and one at/above forecast.
    This gives better coverage around the NWS forecast temperature.

    Returns:
        Tuple of (bucket1, bucket2, total_cost) if opportunity found, else None
    """
    # Build list of bucket markets with prices
    buckets = []
    for market in markets:
        ticker = market.get("ticker")
        title = market.get("yes_sub_title") or market.get("title")
        floor = market.get("floor_strike")
        cap = market.get("cap_strike")
        yes_ask = market.get("yes_ask", 0) or 0
        yes_bid = market.get("yes_bid", 0) or 0

        # Skip if no price available
        if yes_ask <= 0:
            continue

        # Our maker price: bid + 1 (place order just above current bid)
        # This makes us a maker, not a taker
        maker_price = yes_ask
        if yes_bid and yes_bid > 0:
            # Maker intent: price just above bid, but never cross the ask
            maker_price = min(yes_bid + 1, yes_ask)
        # Clamp to valid Kalshi price bounds (1-99)
        maker_price = max(1, min(99, int(maker_price)))

        # Skip suspiciously low prices (<15¢) - not worth the money
        if maker_price < 15:
            continue

        # Calculate midpoint for sorting
        if floor is not None and cap is not None:
            midpoint = (floor + cap) / 2
        elif floor is None and cap is not None:
            midpoint = cap - 0.5  # "X or below" bucket
        elif cap is None and floor is not None:
            midpoint = floor + 0.5  # "X+1 or above" bucket
        else:
            continue

        buckets.append({
            "ticker": ticker,
            "title": title,
            "floor": floor,
            "cap": cap,
            "yes_ask": yes_ask,
            "yes_bid": yes_bid,
            "maker_price": maker_price,
            "midpoint": midpoint,
        })

    # Sort by midpoint (temperature)
    buckets.sort(key=lambda x: x["midpoint"])

    # Find the bucket containing the forecast
    forecast_bucket_idx = None
    for i, bucket in enumerate(buckets):
        floor = bucket["floor"]
        cap = bucket["cap"]

        # Check if forecast is in this bucket
        if floor is not None and cap is not None:
            if floor <= forecast_temp < cap:
                forecast_bucket_idx = i
                break
        elif floor is None and cap is not None:
            if forecast_temp < cap:
                forecast_bucket_idx = i
                break
        elif cap is None and floor is not None:
            if forecast_temp >= floor:
                forecast_bucket_idx = i
                break

    # If forecast not in any bucket, find closest
    if forecast_bucket_idx is None:
        best_distance = float('inf')
        for i, bucket in enumerate(buckets):
            distance = abs(bucket["midpoint"] - forecast_temp)
            if distance < best_distance:
                best_distance = distance
                forecast_bucket_idx = i

    if forecast_bucket_idx is None:
        return None

    # Build candidate pairs that BRACKET the forecast
    # Priority 1: (bucket below forecast, bucket containing forecast)
    # Priority 2: (bucket containing forecast, bucket above forecast)
    # Priority 3: Any adjacent pair near forecast
    candidate_pairs = []

    # Check if there's a bucket below that we can pair with forecast bucket
    if forecast_bucket_idx > 0:
        # Pair: (below, forecast_bucket)
        candidate_pairs.append((forecast_bucket_idx - 1, forecast_bucket_idx, "bracket_below"))

    # Check if there's a bucket above that we can pair with forecast bucket
    if forecast_bucket_idx + 1 < len(buckets):
        # Pair: (forecast_bucket, above)
        candidate_pairs.append((forecast_bucket_idx, forecast_bucket_idx + 1, "bracket_above"))

    # Evaluate each candidate pair
    valid_pairs = []
    for i, j, pair_type in candidate_pairs:
        b1, b2 = buckets[i], buckets[j]

        # Check both maker prices are under individual price limit
        if b1["maker_price"] > MAX_BUCKET_PRICE or b2["maker_price"] > MAX_BUCKET_PRICE:
            continue

        # Check total cost is under limit
        total_cost = b1["maker_price"] + b2["maker_price"]
        if total_cost > MAX_TOTAL_COST:
            continue

        # Calculate how well this pair covers the forecast
        # Lower bucket's cap to upper bucket's floor (or cap)
        coverage_low = b1["floor"] if b1["floor"] is not None else -999
        coverage_high = b2["cap"] if b2["cap"] is not None else 999

        # Score: prefer pairs where forecast is well-centered
        distance_to_low = forecast_temp - coverage_low if coverage_low != -999 else 999
        distance_to_high = coverage_high - forecast_temp if coverage_high != 999 else 999
        coverage_score = min(distance_to_low, distance_to_high)  # Higher = better centered

        valid_pairs.append({
            "pair": (b1, b2),
            "total_cost": total_cost,
            "pair_type": pair_type,
            "coverage_score": coverage_score,
        })

    if not valid_pairs:
        return None

    # Sort by: 1) coverage_score (higher = better), 2) total_cost (lower = better)
    valid_pairs.sort(key=lambda x: (-x["coverage_score"], x["total_cost"]))

    best = valid_pairs[0]
    return (best["pair"][0], best["pair"][1], best["total_cost"])


def execute_hedge_trade(series_ticker, bucket1, bucket2, total_cost):
    """Execute the hedged YES trade on both buckets using MAKER orders."""
    market_info = MARKETS.get(series_ticker, {})
    city = market_info.get("city", series_ticker)
    temp_type = market_info.get("type", "temp").upper()

    price1 = bucket1["maker_price"]
    price2 = bucket2["maker_price"]

    print(f"\n  HEDGE OPPORTUNITY: {city} {temp_type}")
    print(f"    Bucket 1: {bucket1['title']} @ {price1}¢ (bid={bucket1['yes_bid']}¢, ask={bucket1['yes_ask']}¢)")
    print(f"    Bucket 2: {bucket2['title']} @ {price2}¢ (bid={bucket2['yes_bid']}¢, ask={bucket2['yes_ask']}¢)")
    print(f"    Total cost: {total_cost}¢ (MAKER orders)")
    print(f"    Guaranteed profit if either hits: {100 - total_cost}¢")

    # Buy YES on bucket 1 at maker price (bid + 1)
    print(f"\n    Placing LIMIT order on {bucket1['title']} @ {price1}¢...")
    result1 = buy(bucket1["ticker"], "yes", 1, price1)
    if result1:
        order_id1 = result1.get("order_id") or result1.get("id") or (result1.get("order", {}) if isinstance(result1.get("order", {}), dict) else {}).get("order_id")
        add_pending_order(bucket1["ticker"], "yes", price1, 1, order_id=order_id1)
        reconcile_pending_orders(get_temperature_positions)
        positions = load_positions()
        key = f"{bucket1['ticker']}_yes"
        if key in positions:
            positions[key]["floor"] = bucket1["floor"]
            positions[key]["cap"] = bucket1["cap"]
            positions[key]["title"] = bucket1["title"]
            positions[key]["hedge_pair"] = bucket2["ticker"]
            positions[key]["series"] = series_ticker
            save_positions(positions)
        positions_now = load_positions()
        st = positions_now.get(key, {}).get("status", "pending")
        filled_ct = positions_now.get(key, {}).get("count", 0)
        print(f"    SUCCESS: Limit order placed @ {price1}¢ (status={st}, filled={filled_ct})")
    else:
        print(f"    FAILED to place order on bucket 1")
        return False

    # Buy YES on bucket 2 at maker price (bid + 1)
    print(f"\n    Placing LIMIT order on {bucket2['title']} @ {price2}¢...")
    result2 = buy(bucket2["ticker"], "yes", 1, price2)
    if result2:
        order_id2 = result2.get("order_id") or result2.get("id") or (result2.get("order", {}) if isinstance(result2.get("order", {}), dict) else {}).get("order_id")
        add_pending_order(bucket2["ticker"], "yes", price2, 1, order_id=order_id2)
        reconcile_pending_orders(get_temperature_positions)
        positions = load_positions()
        key = f"{bucket2['ticker']}_yes"
        if key in positions:
            positions[key]["floor"] = bucket2["floor"]
            positions[key]["cap"] = bucket2["cap"]
            positions[key]["title"] = bucket2["title"]
            positions[key]["hedge_pair"] = bucket1["ticker"]
            positions[key]["series"] = series_ticker
            save_positions(positions)
        positions_now = load_positions()
        st = positions_now.get(key, {}).get("status", "pending")
        filled_ct = positions_now.get(key, {}).get("count", 0)
        print(f"    SUCCESS: Limit order placed @ {price2}¢ (status={st}, filled={filled_ct})")
    else:
        print(f"    FAILED to place order on bucket 2 (bucket 1 order already placed!)")
        return False

    return True


def scan_all_markets(days_ahead=1):
    """
    Scan all temperature markets for hedge opportunities.

    Returns:
        List of (series_ticker, bucket1, bucket2, total_cost, forecast) tuples
    """
    opportunities = []

    for series_ticker, market_info in MARKETS.items():
        city = market_info["city"]
        temp_type = market_info["type"]

        # Get forecast
        if days_ahead == 0:
            forecast = get_today_forecast(series_ticker)
        else:
            forecast = get_tomorrow_forecast(series_ticker)

        if not forecast:
            continue

        forecast_temp = forecast["forecast_temp"]

        # Get market data
        event, markets = get_market_data_for_series(series_ticker, days_ahead)
        if not markets:
            continue

        # Look for hedge opportunity
        opportunity = find_hedge_opportunity(markets, forecast_temp)
        if opportunity:
            bucket1, bucket2, total_cost = opportunity
            opportunities.append({
                "series": series_ticker,
                "city": city,
                "type": temp_type,
                "forecast": forecast_temp,
                "bucket1": bucket1,
                "bucket2": bucket2,
                "total_cost": total_cost,
                "profit": 100 - total_cost,
            })

    # Sort by profit (highest first)
    opportunities.sort(key=lambda x: x["profit"], reverse=True)
    return opportunities


# ============================================================
# PARTIAL FILL MONITORING
# ============================================================

# Configuration
REPRICE_INCREMENT = 1  # Cents to add when repricing


def get_wait_minutes_for_hours(hours_until_close):
    """
    Scale wait period based on time to close.

    - >12 hours: wait 2 hours before first evaluation
    - 6-12 hours: wait 1 hour
    - 2-6 hours: wait 20 minutes
    - <2 hours: no wait (urgent)

    Returns:
        Wait time in minutes
    """
    if hours_until_close is None:
        return 60  # Default to 1 hour if unknown

    if hours_until_close > 12:
        return 120  # 2 hours
    elif hours_until_close > 6:
        return 60   # 1 hour
    elif hours_until_close > 2:
        return 20   # 20 minutes
    else:
        return 0    # Urgent - no wait


def parse_iso_datetime(dt_string):
    """Parse ISO datetime string to datetime object."""
    if not dt_string:
        return None
    # Handle various ISO formats
    dt_string = dt_string.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(dt_string)
    except ValueError:
        # Try without timezone
        return datetime.fromisoformat(dt_string.split("+")[0].split("Z")[0])


def get_hours_until_close(ticker):
    """
    Get hours remaining until market closes.

    All temperature markets close at 11:59 PM ET on the event date.
    Extract date from ticker (e.g., KXHIGHTLV-26JAN26-B62.5 -> Jan 26, 2026)

    Ticker format: SERIES-DDMMMDD-BUCKET where:
      - First DD is day of month
      - MMM is month abbreviation
      - Second DD is also day of month (repeated)
      - Year is inferred from current date

    Returns:
        Float hours until close, or None if can't determine
    """
    # Extract date from ticker: KXHIGHTLV-26JAN26-B62.5 -> 26JAN26
    parts = ticker.split("-")
    if len(parts) < 2:
        return None

    date_part = parts[1]  # e.g., "26JAN26" or "26JAN27"
    try:
        # Parse date: format is DDMMMDD (day repeated at end, not year)
        day = int(date_part[5:7])  # Use the SECOND day value (more reliable)
        month_str = date_part[2:5].upper()

        month_map = {
            "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
            "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
        }
        month = month_map.get(month_str)
        if not month:
            return None

        # Infer year from current date (markets are for today/tomorrow)
        now = datetime.now()
        year = now.year

        # Handle year boundary (e.g., Dec 31 looking at Jan 1)
        if month == 1 and now.month == 12:
            year += 1

        # Close time is 11:59 PM ET on that date
        # For local time calculation, assume system is roughly aligned with ET
        # or adjust as needed. Using 23:59 local as approximation.
        close_time = datetime(year, month, day, 23, 59, 0)

        delta = close_time - now
        return delta.total_seconds() / 3600
    except (ValueError, IndexError):
        return None


def find_partial_fill_hedges():
    """
    Find hedges where one leg is filled and the other is pending.

    Returns:
        List of dicts with filled_pos, pending_pos, series info
    """
    positions = load_positions()
    partial_hedges = []

    # Group positions by series
    by_series = {}
    for key, pos in positions.items():
        series = pos.get("series")
        if series:
            if series not in by_series:
                by_series[series] = []
            by_series[series].append((key, pos))

    # Find series where one is filled and one is pending
    for series, pos_list in by_series.items():
        if len(pos_list) != 2:
            continue

        key1, pos1 = pos_list[0]
        key2, pos2 = pos_list[1]

        status1 = pos1.get("status", "filled")
        status2 = pos2.get("status", "filled")

        # Check for partial fill (one filled, one pending)
        if status1 == "filled" and status2 == "pending":
            partial_hedges.append({
                "series": series,
                "filled_key": key1,
                "filled_pos": pos1,
                "pending_key": key2,
                "pending_pos": pos2,
            })
        elif status2 == "filled" and status1 == "pending":
            partial_hedges.append({
                "series": series,
                "filled_key": key2,
                "filled_pos": pos2,
                "pending_key": key1,
                "pending_pos": pos1,
            })

    return partial_hedges


def get_time_since_order(pos):
    """Get minutes since order was placed."""
    timestamp = pos.get("timestamp")
    if not timestamp:
        return float('inf')

    order_time = parse_iso_datetime(timestamp)
    if not order_time:
        return float('inf')

    delta = datetime.now() - order_time
    return delta.total_seconds() / 60


def evaluate_partial_fill(hedge, days_ahead=1):
    """
    Evaluate a partial fill and decide action.

    Returns:
        Dict with action, reason, and details
    """
    filled_pos = hedge["filled_pos"]
    pending_pos = hedge["pending_pos"]
    series = hedge["series"]

    market_info = MARKETS.get(series, {})
    city = market_info.get("city", series)
    temp_type = market_info.get("type", "temp")

    # Get time until close
    hours_until_close = get_hours_until_close(pending_pos["ticker"])

    # Get current market prices for pending leg
    current_prices = get_market_price(pending_pos["ticker"])
    if not current_prices:
        return {"action": "wait", "reason": "Cannot get current prices"}

    current_ask = current_prices.get("yes_ask", 0) or 0
    current_bid = current_prices.get("yes_bid", 0) or 0
    original_limit = pending_pos.get("limit_price", 0)

    # Get current forecast
    if days_ahead == 0:
        forecast = get_today_forecast(series)
    else:
        forecast = get_tomorrow_forecast(series)

    forecast_temp = forecast["forecast_temp"] if forecast else None

    # Calculate new maker price (bid + 1, but don't cross ask)
    new_maker_price = min(current_bid + 1, current_ask) if current_bid > 0 else current_ask
    new_maker_price = max(1, min(99, int(new_maker_price)))

    result = {
        "series": series,
        "city": city,
        "type": temp_type,
        "filled_ticker": filled_pos["ticker"],
        "filled_price": filled_pos.get("limit_price", 0),
        "pending_ticker": pending_pos["ticker"],
        "original_limit": original_limit,
        "current_ask": current_ask,
        "current_bid": current_bid,
        "new_maker_price": new_maker_price,
        "hours_until_close": hours_until_close,
        "forecast": forecast_temp,
        "order_id": pending_pos.get("order_id"),
    }

    # Time-based rules
    if hours_until_close is not None:
        if hours_until_close < 2:
            # DE-RISK: Sell the filled leg
            result["action"] = "derisk"
            result["reason"] = f"<2 hrs to close ({hours_until_close:.1f}h). Selling filled leg to exit."
            return result

        elif hours_until_close < 6:
            # REPRICE: Cancel and replace at new maker price
            if new_maker_price <= original_limit + REPRICE_INCREMENT:
                result["action"] = "reprice"
                result["reason"] = f"2-6 hrs to close ({hours_until_close:.1f}h). Repricing to {new_maker_price}¢"
            else:
                result["action"] = "derisk"
                result["reason"] = f"2-6 hrs to close ({hours_until_close:.1f}h). Price moved too much ({original_limit}¢→{new_maker_price}¢). De-risking."
            return result

    # >6 hours: Evaluate price changes
    price_diff = new_maker_price - original_limit

    if price_diff <= 0:
        # Same or cheaper - keep order
        result["action"] = "wait"
        result["reason"] = f"Price same or better ({original_limit}¢→{new_maker_price}¢). Keeping order."
    elif price_diff == 1:
        # Slightly higher - reprice
        result["action"] = "reprice"
        result["reason"] = f"Price +1¢ ({original_limit}¢→{new_maker_price}¢). Repricing."
    else:
        # Much worse - consider de-risking
        # Check if still within our max budget
        filled_price = filled_pos.get("limit_price", 0)
        new_total = filled_price + new_maker_price

        if new_total <= MAX_TOTAL_COST:
            result["action"] = "reprice"
            result["reason"] = f"Price +{price_diff}¢ but total {new_total}¢ still under budget. Repricing."
        else:
            result["action"] = "derisk"
            result["reason"] = f"Price +{price_diff}¢ ({original_limit}¢→{new_maker_price}¢). New total {new_total}¢ exceeds budget. De-risking."

    return result


def execute_reprice(hedge, new_price):
    """
    Cancel pending order and place new one at new_price.

    Returns:
        True if successful, False otherwise
    """
    pending_pos = hedge["pending_pos"]
    pending_key = hedge["pending_key"]
    order_id = pending_pos.get("order_id")
    ticker = pending_pos["ticker"]

    print(f"  Repricing {ticker}: {pending_pos.get('limit_price', 0)}¢ → {new_price}¢")

    # Cancel existing order
    if order_id:
        if not cancel_order(order_id):
            print(f"    Failed to cancel order {order_id}")
            return False

    # Place new order
    result = buy(ticker, "yes", 1, new_price)
    if result:
        new_order_id = result.get("order_id") or result.get("id") or (result.get("order", {}) if isinstance(result.get("order", {}), dict) else {}).get("order_id")

        # Update positions.json
        positions = load_positions()
        if pending_key in positions:
            positions[pending_key]["limit_price"] = new_price
            positions[pending_key]["order_id"] = new_order_id
            positions[pending_key]["timestamp"] = datetime.now().isoformat()
            positions[pending_key]["reprice_count"] = positions[pending_key].get("reprice_count", 0) + 1
            save_positions(positions)

        print(f"    New order placed at {new_price}¢")
        return True
    else:
        print(f"    Failed to place new order")
        return False


def execute_derisk(hedge):
    """
    Sell the filled leg to exit the partial position.

    Returns:
        True if successful, False otherwise
    """
    filled_pos = hedge["filled_pos"]
    filled_key = hedge["filled_key"]
    pending_pos = hedge["pending_pos"]
    pending_key = hedge["pending_key"]

    ticker = filled_pos["ticker"]

    # Get current bid to sell at
    prices = get_market_price(ticker)
    if not prices:
        print(f"    Cannot get prices for {ticker}")
        return False

    current_bid = prices.get("yes_bid", 0) or 0
    if current_bid <= 0:
        print(f"    No bid available for {ticker}")
        return False

    entry_price = filled_pos.get("limit_price", 0)
    pnl = current_bid - entry_price  # Positive = profit, negative = loss

    if pnl >= 0:
        print(f"  De-risking: Selling {ticker} @ {current_bid}¢ (entry: {entry_price}¢, profit: +{pnl}¢)")
    else:
        print(f"  De-risking: Selling {ticker} @ {current_bid}¢ (entry: {entry_price}¢, loss: {pnl}¢)")

    # Cancel pending order first
    pending_order_id = pending_pos.get("order_id")
    if pending_order_id:
        cancel_order(pending_order_id)

    # Sell the filled position
    result = sell(ticker, "yes", 1, current_bid)
    if result:
        # Remove both positions from tracking
        positions = load_positions()

        # Mark as de-risked instead of deleting (for audit trail)
        if filled_key in positions:
            positions[filled_key]["status"] = "derisk_sold"
            positions[filled_key]["sold_price"] = current_bid
            positions[filled_key]["sold_timestamp"] = datetime.now().isoformat()
            positions[filled_key]["pnl"] = pnl  # Positive = profit, negative = loss

        if pending_key in positions:
            positions[pending_key]["status"] = "derisk_cancelled"
            positions[pending_key]["cancelled_timestamp"] = datetime.now().isoformat()

        save_positions(positions)
        if pnl >= 0:
            print(f"    Position de-risked. Profit: +{pnl}¢")
        else:
            print(f"    Position de-risked. Loss: {pnl}¢")
        return True
    else:
        print(f"    Failed to sell position")
        return False


def monitor_partial_fills(days_ahead=1):
    """
    Main function to monitor and manage partial fills.

    Call this periodically (e.g., every 5-10 minutes) to check partial fills.
    """
    print("\n" + "=" * 60)
    print("PARTIAL FILL MONITOR")
    print("=" * 60)

    # First, reconcile with Kalshi to get latest fill status
    reconcile_pending_orders(get_temperature_positions)

    # Find partial fills
    partial_hedges = find_partial_fill_hedges()

    if not partial_hedges:
        print("No partial fills detected. All hedges complete or pending.")
        return []

    print(f"\nFound {len(partial_hedges)} partial fill(s):\n")

    actions_taken = []

    for hedge in partial_hedges:
        filled_pos = hedge["filled_pos"]
        pending_pos = hedge["pending_pos"]
        series = hedge["series"]

        market_info = MARKETS.get(series, {})
        city = market_info.get("city", series)
        temp_type = market_info.get("type", "temp").upper()

        print(f"{city} {temp_type}:")
        print(f"  Filled: {filled_pos.get('title', filled_pos['ticker'])} @ {filled_pos.get('limit_price', 0)}¢")
        print(f"  Pending: {pending_pos.get('title', pending_pos['ticker'])} @ {pending_pos.get('limit_price', 0)}¢")

        # Get hours to close first to determine wait period
        hours_until_close = get_hours_until_close(pending_pos["ticker"])
        wait_minutes = get_wait_minutes_for_hours(hours_until_close)

        # Check wait period (scaled by time to close)
        minutes_waiting = get_time_since_order(pending_pos)
        print(f"  Time waiting: {minutes_waiting:.0f} min (required: {wait_minutes} min for {hours_until_close:.1f}h to close)" if hours_until_close else f"  Time waiting: {minutes_waiting:.0f} min")

        if minutes_waiting < wait_minutes:
            remaining = wait_minutes - minutes_waiting
            print(f"  Action: WAIT ({remaining:.0f} min remaining before evaluation)")
            actions_taken.append({
                "series": series,
                "action": "wait",
                "reason": f"Wait period ({remaining:.0f} min remaining)"
            })
            continue

        # Evaluate and decide action
        evaluation = evaluate_partial_fill(hedge, days_ahead)
        action = evaluation["action"]
        reason = evaluation["reason"]

        print(f"  Hours to close: {evaluation.get('hours_until_close', 'N/A')}")
        print(f"  Current market: bid={evaluation.get('current_bid', 0)}¢, ask={evaluation.get('current_ask', 0)}¢")
        print(f"  Decision: {action.upper()} - {reason}")

        if action == "reprice":
            success = execute_reprice(hedge, evaluation["new_maker_price"])
            evaluation["success"] = success
        elif action == "derisk":
            success = execute_derisk(hedge)
            evaluation["success"] = success
        else:
            evaluation["success"] = True  # Wait is always "successful"

        actions_taken.append(evaluation)
        print()

    return actions_taken


def run_strategy(days_ahead=1):
    """Main strategy loop - scans all markets."""
    print("=" * 60)
    print("TEMPERATURE TRADING STRATEGY - HEDGED YES (ALL MARKETS)")
    print("=" * 60)
    print(f"\nStrategy:")
    print(f"  - Scan {len(MARKETS)} temperature markets")
    print(f"  - Buy YES on 2 adjacent buckets around forecast")
    print(f"  - Only enter if both buckets ≤{MAX_BUCKET_PRICE}¢ each")
    print(f"  - Max total cost: {MAX_TOTAL_COST}¢ for both")
    print(f"  - Win 100¢ if temp lands in either bucket")
    print(f"  - Guaranteed profit: 100¢ - cost = at least {100 - MAX_TOTAL_COST}¢")
    print("=" * 60)

    target_date = datetime.now() + timedelta(days=days_ahead)
    date_str = target_date.strftime("%Y-%m-%d")

    print(f"\nTarget date: {date_str}")
    print(f"Scanning {len(MARKETS)} markets...")

    # Main loop
    iteration = 0
    trades_executed = set()  # Track which series we've traded

    while True:
        iteration += 1
        now = datetime.now()

        print(f"\n{'='*60}")
        print(f"[{now.strftime('%H:%M:%S')}] Iteration {iteration}")

        # Scan all markets for opportunities
        print(f"\n  Scanning all markets for hedge opportunities...")
        opportunities = scan_all_markets(days_ahead)

        if opportunities:
            print(f"\n  Found {len(opportunities)} opportunities:")
            for opp in opportunities:
                already_traded = "✓" if opp["series"] in trades_executed else ""
                print(f"    {opp['city']} {opp['type'].upper()}: "
                      f"{opp['bucket1']['yes_ask']}¢ + {opp['bucket2']['yes_ask']}¢ = {opp['total_cost']}¢ "
                      f"(profit: {opp['profit']}¢) {already_traded}")

            # Execute trades on opportunities we haven't traded yet
            for opp in opportunities:
                if opp["series"] not in trades_executed:
                    print(f"\n  Executing trade on {opp['city']} {opp['type'].upper()}...")
                    if execute_hedge_trade(opp["series"], opp["bucket1"], opp["bucket2"], opp["total_cost"]):
                        trades_executed.add(opp["series"])
                    break  # Only execute one trade per iteration
        else:
            print(f"\n  No hedge opportunities found (buckets too expensive)")

        # Show current positions
        positions = load_positions()
        if positions:
            print(f"\n  Active positions: {len(positions)}")
            total_cost = 0
            total_value = 0

            # Group by series
            by_series = {}
            for key, pos in positions.items():
                series = pos.get("series", "unknown")
                if series not in by_series:
                    by_series[series] = []
                by_series[series].append(pos)

            for series, series_positions in by_series.items():
                market_info = MARKETS.get(series, {})
                city = market_info.get("city", series)
                temp_type = market_info.get("type", "").upper()
                print(f"\n    {city} {temp_type}:")

                series_cost = 0
                series_value = 0
                for pos in series_positions:
                    ticker = pos["ticker"]
                    entry_price = pos["entry_price"]
                    count = pos["count"]

                    prices = get_market_price(ticker)
                    if prices:
                        current_bid = prices.get("yes_bid", 0) or 0
                        pnl_per = current_bid - entry_price
                        cost = entry_price * count
                        value = current_bid * count
                        series_cost += cost
                        series_value += value
                        total_cost += cost
                        total_value += value

                        print(f"      {pos.get('title', ticker)}: @ {entry_price}¢ -> {current_bid}¢ ({pnl_per:+}¢)")
                    else:
                        print(f"      {pos.get('title', ticker)}: @ {entry_price}¢")

                if series_cost > 0:
                    print(f"      Subtotal: {series_cost}¢ -> {series_value}¢ | If win: {series_cost}¢ -> 100¢ (+{100-series_cost}¢)")

            if total_cost > 0:
                net_pnl = total_value - total_cost
                print(f"\n    ════════════════════════════════════════")
                print(f"    TOTAL: ${total_cost/100:.2f} invested -> ${total_value/100:.2f} value (${net_pnl/100:+.2f})")

        # Summary of trades executed
        if trades_executed:
            print(f"\n  Trades executed: {len(trades_executed)} markets")
            for series in trades_executed:
                market_info = MARKETS.get(series, {})
                print(f"    ✓ {market_info.get('city', series)} {market_info.get('type', '').upper()}")

        # Wait for next check
        print(f"\n  Next check in {FORECAST_CHECK_INTERVAL//60} min...")
        time.sleep(FORECAST_CHECK_INTERVAL)


def run_once(days_ahead=1):
    """Scan all markets, execute all trades, then exit."""
    print("=" * 60)
    print("TEMPERATURE TRADING - HEDGED YES (SINGLE RUN)")
    print("=" * 60)
    print(f"\nStrategy:")
    print(f"  - Scan {len(MARKETS)} temperature markets")
    print(f"  - Buy YES on 2 adjacent buckets around forecast")
    print(f"  - Only enter if both buckets ≤{MAX_BUCKET_PRICE}¢ each")
    print(f"  - Max total cost: {MAX_TOTAL_COST}¢ for both")
    print("=" * 60)

    # Check existing positions to avoid duplicates
    existing_positions = load_positions()
    already_traded = set()
    for key, pos in existing_positions.items():
        series = pos.get("series")
        if series:
            already_traded.add(series)

    if already_traded:
        print(f"\nSkipping {len(already_traded)} markets with existing positions:")
        for series in sorted(already_traded):
            market_info = MARKETS.get(series, {})
            print(f"  - {market_info.get('city', series)} {market_info.get('type', '').upper()}")

    target_date = datetime.now() + timedelta(days=days_ahead)
    print(f"\nTarget date: {target_date.strftime('%Y-%m-%d')}")
    print(f"Scanning {len(MARKETS) - len(already_traded)} remaining markets...\n")

    opportunities = scan_all_markets(days_ahead)

    # Filter out markets we already have positions in
    opportunities = [opp for opp in opportunities if opp["series"] not in already_traded]

    if not opportunities:
        print("No hedge opportunities found (all buckets too expensive)")
        return []

    print(f"Found {len(opportunities)} hedge opportunities:\n")
    for opp in opportunities:
        print(f"{opp['city']} {opp['type'].upper()}:")
        print(f"  Forecast: {opp['forecast']}°F")
        print(f"  Bucket 1: {opp['bucket1']['title']} @ {opp['bucket1']['maker_price']}¢ (bid+1)")
        print(f"  Bucket 2: {opp['bucket2']['title']} @ {opp['bucket2']['maker_price']}¢ (bid+1)")
        print(f"  Total: {opp['total_cost']}¢ | Profit if win: {opp['profit']}¢")
        print()

    # Execute all trades
    print("=" * 60)
    print("EXECUTING TRADES")
    print("=" * 60)

    executed = []
    total_spent = 0

    for opp in opportunities:
        print(f"\n{opp['city']} {opp['type'].upper()}:")
        if execute_hedge_trade(opp["series"], opp["bucket1"], opp["bucket2"], opp["total_cost"]):
            executed.append(opp)
            total_spent += opp["total_cost"]

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Trades executed: {len(executed)}/{len(opportunities)}")
    print(f"Total spent: ${total_spent/100:.2f}")
    if executed:
        potential_profit = len(executed) * 100 - total_spent
        print(f"If all win: ${len(executed):.2f} return (${potential_profit/100:.2f} profit)")
    print()

    for opp in executed:
        print(f"  ✓ {opp['city']} {opp['type'].upper()}: {opp['total_cost']}¢")

    # Check for partial fills and manage them
    print("\n" + "=" * 60)
    print("CHECKING PARTIAL FILLS")
    print("=" * 60)
    monitor_partial_fills(days_ahead)

    return executed


def run_monitor_loop(days_ahead=1, interval_minutes=10):
    """
    Continuous loop to monitor partial fills.

    Run this after run_once to keep monitoring until all hedges complete or de-risk.
    """
    print("=" * 60)
    print("PARTIAL FILL MONITOR - CONTINUOUS MODE")
    print(f"Checking every {interval_minutes} minutes")
    print("=" * 60)

    while True:
        actions = monitor_partial_fills(days_ahead)

        # Check if there are any partial fills left
        partial_hedges = find_partial_fill_hedges()
        if not partial_hedges:
            print("\nAll hedges complete or resolved. Exiting monitor.")
            break

        # Count active partials (not de-risked)
        positions = load_positions()
        active_partials = 0
        for h in partial_hedges:
            pending_status = positions.get(h["pending_key"], {}).get("status", "")
            if pending_status not in ("derisk_cancelled", "derisk_sold"):
                active_partials += 1

        if active_partials == 0:
            print("\nAll partial fills resolved. Exiting monitor.")
            break

        print(f"\n{active_partials} partial fill(s) remaining. Next check in {interval_minutes} min...")
        time.sleep(interval_minutes * 60)


if __name__ == "__main__":
    import sys

    days = 0  # Default to today
    if "--today" in sys.argv:
        days = 0
    elif "--tomo" in sys.argv:
        days = 1

    if "--monitor" in sys.argv:
        # Just run the partial fill monitor
        monitor_partial_fills(days_ahead=days)
    elif "--monitor-loop" in sys.argv:
        # Run continuous monitor loop
        run_monitor_loop(days_ahead=days)
    else:
        # Normal run: place trades then check partial fills
        run_once(days_ahead=days)
