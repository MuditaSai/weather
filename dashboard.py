"""
Position Dashboard - Web UI for tracking positions.

Run: python dashboard.py
Open: http://localhost:8080
"""

import json
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
from datetime import datetime, timedelta

POSITIONS_FILE = Path(__file__).parent / "positions.json"
FORECAST_CACHE = {}  # Cache forecasts to avoid repeated API calls

CITY_MAP = {
    "KXHIGHDEN": "Denver HIGH",
    "KXHIGHAUS": "Austin HIGH",
    "KXHIGHNY": "New York HIGH",
    "KXHIGHMIA": "Miami HIGH",
    "KXHIGHTNOLA": "New Orleans HIGH",
    "KXHIGHLAX": "Los Angeles HIGH",
    "KXHIGHCHI": "Chicago HIGH",
    "KXHIGHPHIL": "Philadelphia HIGH",
    "KXHIGHTSEA": "Seattle HIGH",
    "KXHIGHTSFO": "San Francisco HIGH",
    "KXHIGHTDC": "Washington DC HIGH",
    "KXHIGHTLV": "Las Vegas HIGH",
    "KXLOWTNYC": "New York LOW",
    "KXLOWTLAX": "Los Angeles LOW",
    "KXLOWTDEN": "Denver LOW",
    "KXLOWTCHI": "Chicago LOW",
    "KXLOWTMIA": "Miami LOW",
    "KXLOWTPHIL": "Philadelphia LOW",
    "KXLOWTAUS": "Austin LOW",
}


def load_positions():
    try:
        with open(POSITIONS_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def sync_with_kalshi():
    """Sync positions.json with Kalshi API."""
    from orders import get_temperature_positions
    from monitor import reconcile_pending_orders
    reconcile_pending_orders(get_temperature_positions)


def get_forecast_for_series(series, ticker):
    """
    Get NWS forecast for a series, with caching.

    Returns dict with forecast_temp, or None if unavailable.
    Also returns market_status: "future", "settling", or "settled"
    """
    global FORECAST_CACHE

    # Determine target date from ticker
    # Ticker format: KXHIGHDEN-26JAN27-B47.5 -> Jan 27
    parts = ticker.split("-")
    if len(parts) < 2:
        return None

    date_part = parts[1]  # e.g., "26JAN27"
    try:
        day = int(date_part[5:7])  # Use second day value
        month_str = date_part[2:5].upper()
        month_map = {
            "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
            "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
        }
        month = month_map.get(month_str, 1)

        now = datetime.now()
        year = now.year
        if month == 1 and now.month == 12:
            year += 1

        from datetime import date
        target_date = date(year, month, day)
    except (ValueError, IndexError):
        return None

    # Check if market is settling or settled (today or past)
    today = datetime.now().date()
    if target_date < today:
        return {"market_status": "settled", "target_date": target_date}
    elif target_date == today:
        # Market is for today - check if we're past typical settlement time (after ~6 PM local)
        if datetime.now().hour >= 18:
            return {"market_status": "settling", "target_date": target_date}
        # Otherwise still use forecast but mark as today

    cache_key = f"{series}_{target_date}"
    if cache_key in FORECAST_CACHE:
        cached = FORECAST_CACHE[cache_key]
        cached["market_status"] = "today" if target_date == today else "future"
        return cached

    try:
        from nws_forecast import get_forecast_for_market
        forecast = get_forecast_for_market(series, target_date)
        if forecast:
            forecast["market_status"] = "today" if target_date == today else "future"
            FORECAST_CACHE[cache_key] = forecast
        return forecast
    except Exception as e:
        print(f"Error getting forecast for {series}: {e}")
        return None


def check_market_agreement(ticker):
    """
    Check if a market has reached 99% agreement (practically settled).

    Returns:
        dict with:
            - agreed: True if YES price >= 99 or YES price <= 1
            - winning: True if this bucket is winning (YES >= 99), False if losing (YES <= 1)
            - yes_price: Current YES price
    """
    try:
        from orders import get_market_price
        prices = get_market_price(ticker)
        if not prices:
            return {"agreed": False, "winning": None, "yes_price": None}

        # Use yes_bid as the current YES price (what you could sell for)
        # If yes_bid >= 99, the market has essentially settled YES (this bucket wins)
        # If yes_ask <= 1, the market has essentially settled NO (this bucket loses)
        yes_bid = prices.get("yes_bid") or 0
        yes_ask = prices.get("yes_ask") or 100

        if yes_bid >= 99:
            return {"agreed": True, "winning": True, "yes_price": yes_bid}
        elif yes_ask <= 1:
            return {"agreed": True, "winning": False, "yes_price": yes_ask}
        else:
            return {"agreed": False, "winning": None, "yes_price": yes_bid}
    except Exception as e:
        print(f"Error checking market agreement for {ticker}: {e}")
        return {"agreed": False, "winning": None, "yes_price": None}


def sell_contract(ticker, pos_key):
    """
    Sell a YES contract at the current bid price.

    Args:
        ticker: The ticker to sell
        pos_key: Key in positions.json

    Returns:
        dict with success, message, and details
    """
    from orders import get_market_price, get_auth_headers
    from monitor import load_positions, save_positions
    from config import API_BASE
    import requests

    # Get current bid
    prices = get_market_price(ticker)
    if not prices:
        return {"success": False, "message": f"Cannot get prices for {ticker}"}

    current_bid = prices.get("yes_bid") or 0
    if current_bid <= 0:
        return {"success": False, "message": f"No bid available for {ticker}"}

    positions = load_positions()
    pos = positions.get(pos_key, {})
    entry_price = pos.get("limit_price", 0)
    pnl = current_bid - entry_price

    # Place limit sell order for YES contract
    # To sell YES at the bid, we need action="sell", side="yes", yes_price=bid
    path = "/trade-api/v2/portfolio/orders"
    url = f"{API_BASE}/portfolio/orders"

    body = {
        "ticker": ticker,
        "side": "yes",
        "action": "sell",
        "count": 1,
        "type": "limit",
        "yes_price": current_bid,
    }

    headers = get_auth_headers("POST", path)
    response = requests.post(url, json=body, headers=headers)

    if response.status_code not in (200, 201):
        return {"success": False, "message": f"Failed to place sell order: {response.status_code} - {response.text}"}

    result = response.json()
    order_data = result.get("order", result)
    order_id = order_data.get("order_id") or order_data.get("id")

    # Update positions.json
    positions = load_positions()
    if pos_key in positions:
        positions[pos_key]["status"] = "sold"
        positions[pos_key]["sold_price"] = current_bid
        positions[pos_key]["sold_timestamp"] = datetime.now().isoformat()
        positions[pos_key]["pnl"] = pnl
        positions[pos_key]["sell_order_id"] = order_id
        save_positions(positions)

    # Record to trade history
    try:
        from trade_history import generate_trade_id, record_win
        from nws_forecast import get_forecast_for_market
        from datetime import date

        series = pos.get("series", "")
        trade_id = generate_trade_id(series, ticker)

        # Get current forecast for the trade
        try:
            parts = ticker.split("-")
            if len(parts) >= 2:
                date_part = parts[1]
                day = int(date_part[5:7])
                month_str = date_part[2:5].upper()
                month_map = {
                    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
                    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
                }
                month = month_map.get(month_str, 1)
                year = datetime.now().year
                if month == 1 and datetime.now().month == 12:
                    year += 1
                target_date = date(year, month, day)
                forecast = get_forecast_for_market(series, target_date)
                actual_temp = forecast.get("forecast_temp") if forecast else None
        except Exception:
            actual_temp = None

        # Record the win (selling at high price = win)
        record_win(trade_id, pos_key, actual_temp=actual_temp, sold_price=current_bid)
    except Exception as e:
        print(f"Warning: Could not record to trade history: {e}")

    return {
        "success": True,
        "message": f"Sell order placed at {current_bid}¢",
        "ticker": ticker,
        "price": current_bid,
        "pnl": pnl,
        "order_id": order_id
    }




def evaluate_hedge_likelihood(pos1, pos2, forecast_temp):
    """
    Evaluate how likely a hedge is to win based on forecast.

    Returns:
        dict with:
            - in_range: True if forecast is within our hedge buckets
            - distance: How far forecast is from nearest bucket edge
            - confidence: "high", "medium", "low" based on how centered
    """
    if forecast_temp is None:
        return {"in_range": None, "distance": None, "confidence": None}

    # Get bucket ranges
    floor1 = pos1.get("floor")
    cap1 = pos1.get("cap")
    floor2 = pos2.get("floor")
    cap2 = pos2.get("cap")

    # Determine the full hedge range
    # Handle "X or above" (cap=None) and "X or below" (floor=None) buckets
    floors = [f for f in [floor1, floor2] if f is not None]
    caps = [c for c in [cap1, cap2] if c is not None]

    if not floors and not caps:
        return {"in_range": None, "distance": None, "confidence": None}

    hedge_min = min(floors) if floors else float('-inf')
    hedge_max = max(caps) if caps else float('inf')

    # Check if forecast is in range
    in_range = hedge_min <= forecast_temp < hedge_max

    # Calculate distance from nearest edge
    if in_range:
        dist_to_min = forecast_temp - hedge_min
        dist_to_max = hedge_max - forecast_temp
        distance = min(dist_to_min, dist_to_max)

        # Confidence based on how centered the forecast is
        if distance >= 1.5:
            confidence = "high"
        elif distance >= 0.5:
            confidence = "medium"
        else:
            confidence = "low"
    else:
        # Outside range - how far off?
        if forecast_temp < hedge_min:
            distance = hedge_min - forecast_temp
        else:
            distance = forecast_temp - hedge_max
        confidence = "miss"

    return {
        "in_range": in_range,
        "distance": round(distance, 1),
        "confidence": confidence,
        "hedge_min": hedge_min,
        "hedge_max": hedge_max,
    }


def generate_html():
    positions = load_positions()

    # Group by series (include keys for sell buttons)
    hedges = {}
    for key, pos in positions.items():
        series = pos.get("series", "UNKNOWN")
        if series not in hedges:
            hedges[series] = []
        hedges[series].append((key, pos))

    # Calculate totals
    total_cost = 0
    total_potential_profit = 0
    filled_count = 0
    pending_count = 0
    derisk_count = 0
    derisk_loss = 0
    at_risk_cost = 0  # Cost in partial fills (only 1 leg filled)
    won_count = 0          # Hedges that won (99% agreement)
    lost_count = 0         # Hedges that lost (99% agreement)
    won_profit = 0         # Total profit from won hedges
    lost_loss = 0          # Total loss from lost hedges

    rows_html = ""
    derisk_rows_html = ""
    for series in sorted(hedges.keys()):
        pair = hedges[series]
        if len(pair) < 2:
            continue

        pair.sort(key=lambda x: x[1].get("ticker", ""))
        (key1, pos1), (key2, pos2) = pair[0], pair[1]

        city = CITY_MAP.get(series, series)
        title1 = pos1.get("title", "?")
        title2 = pos2.get("title", "?")

        price1 = pos1.get("limit_price", 0)
        price2 = pos2.get("limit_price", 0)
        total_hedge_cost = price1 + price2

        status1 = pos1.get("status", "unknown")
        status2 = pos2.get("status", "unknown")

        # Check if this hedge was sold as a winner (status "won_sold" or "sold")
        is_won_sold = status1 in ("won_sold", "sold") or status2 in ("won_sold", "sold")

        if is_won_sold:
            # Use theoretical profit: 100 - total_hedge_cost
            pnl = 100 - total_hedge_cost
            sold_price = 0
            winning_title = ""
            for p in [pos1, pos2]:
                if p.get("status") in ("won_sold", "sold"):
                    sold_price = p.get("sold_price", 99)
                    winning_title = p.get("title", "?")
            won_count += 1
            won_profit += pnl

            ticker1 = pos1.get("ticker", "")
            import re
            event_ticker = re.sub(r'-[BT][\d.-]+$', '', ticker1)
            series_lower = series.lower()
            slug_map = {
                "KXHIGHDEN": "highest-temperature-in-denver",
                "KXHIGHAUS": "highest-temperature-in-austin",
                "KXHIGHNY": "highest-temperature-in-new-york",
                "KXHIGHMIA": "highest-temperature-in-miami",
                "KXHIGHTNOLA": "highest-temperature-in-new-orleans",
                "KXHIGHLAX": "highest-temperature-in-los-angeles",
                "KXHIGHCHI": "highest-temperature-in-chicago",
                "KXHIGHPHIL": "highest-temperature-in-philadelphia",
                "KXHIGHTSEA": "highest-temperature-in-seattle",
                "KXHIGHTSFO": "highest-temperature-in-san-francisco",
                "KXHIGHTDC": "highest-temperature-in-washington-dc",
                "KXHIGHTLV": "highest-temperature-in-las-vegas",
                "KXLOWTNYC": "lowest-temperature-in-new-york",
                "KXLOWTLAX": "lowest-temperature-in-los-angeles",
                "KXLOWTDEN": "lowest-temperature-in-denver",
                "KXLOWTCHI": "lowest-temperature-in-chicago",
                "KXLOWTMIA": "lowest-temperature-in-miami",
                "KXLOWTPHIL": "lowest-temperature-in-philadelphia",
                "KXLOWTAUS": "lowest-temperature-in-austin",
            }
            slug = slug_map.get(series, "temperature")
            event_url = f"https://kalshi.com/markets/{series_lower}/{slug}/{event_ticker.lower()}"

            rows_html += f"""
            <tr class="hedge-row won">
                <td class="event-cell">
                    <a href="{event_url}" target="_blank" class="event-link">
                        <div class="event-name">{city}</div>
                        <div class="hedge-pair">{title1} / {title2}</div>
                    </a>
                </td>
                <td class="ticker-cell">
                    <span class="ticker-name">{pos1.get('ticker', '')}</span>
                    <div class="price">{price1}¢</div>
                </td>
                <td class="ticker-cell">
                    <span class="ticker-name">{pos2.get('ticker', '')}</span>
                    <div class="price">{price2}¢</div>
                </td>
                <td class="cost-cell">{total_hedge_cost}¢</td>
                <td class="profit-cell profit">+{pnl}¢</td>
                <td class="loss-cell">--</td>
                <td class="status-cell">
                    <span class="hedge-badge won">WON</span>
                    <div class="outcome-note win">Sold @ {sold_price}¢: {winning_title}</div>
                </td>
                <td class="actions-cell">--</td>
            </tr>
            """
            continue

        # Check if this hedge was de-risked
        is_derisk = status1 in ("derisk_sold", "derisk_cancelled") or status2 in ("derisk_sold", "derisk_cancelled")

        if is_derisk:
            # Calculate de-risk P&L (positive = profit, negative = loss)
            pnl = 0
            sold_price = 0
            for p in [pos1, pos2]:
                if p.get("status") == "derisk_sold":
                    # Support both old 'loss' field and new 'pnl' field
                    if "pnl" in p:
                        pnl += p.get("pnl", 0)
                    else:
                        # Old format: loss was entry - sold (negative when profitable)
                        pnl -= p.get("loss", 0)
                    sold_price = p.get("sold_price", 0)
            derisk_count += 1
            derisk_loss += pnl  # Now tracks net P&L (can be positive)

            ticker1 = pos1.get("ticker", "")
            import re
            event_ticker = re.sub(r'-[BT][\d.-]+$', '', ticker1)
            series_lower = series.lower()
            slug_map = {
                "KXHIGHDEN": "highest-temperature-in-denver",
                "KXHIGHAUS": "highest-temperature-in-austin",
                "KXHIGHNY": "highest-temperature-in-new-york",
                "KXHIGHMIA": "highest-temperature-in-miami",
                "KXHIGHTNOLA": "highest-temperature-in-new-orleans",
                "KXHIGHLAX": "highest-temperature-in-los-angeles",
                "KXHIGHCHI": "highest-temperature-in-chicago",
                "KXHIGHPHIL": "highest-temperature-in-philadelphia",
                "KXHIGHTSEA": "highest-temperature-in-seattle",
                "KXHIGHTSFO": "highest-temperature-in-san-francisco",
                "KXHIGHTDC": "highest-temperature-in-washington-dc",
                "KXHIGHTLV": "highest-temperature-in-las-vegas",
                "KXLOWTNYC": "lowest-temperature-in-new-york",
                "KXLOWTLAX": "lowest-temperature-in-los-angeles",
                "KXLOWTDEN": "lowest-temperature-in-denver",
                "KXLOWTCHI": "lowest-temperature-in-chicago",
                "KXLOWTMIA": "lowest-temperature-in-miami",
                "KXLOWTPHIL": "lowest-temperature-in-philadelphia",
                "KXLOWTAUS": "lowest-temperature-in-austin",
            }
            slug = slug_map.get(series, "temperature")
            event_url = f"https://kalshi.com/markets/{series_lower}/{slug}/{event_ticker.lower()}"

            # Format P&L display
            if pnl >= 0:
                pnl_class = "profit"
                pnl_text = f"+{pnl}¢"
            else:
                pnl_class = "loss"
                pnl_text = f"{pnl}¢"

            derisk_rows_html += f"""
            <tr class="hedge-row derisk">
                <td class="event-cell">
                    <a href="{event_url}" target="_blank" class="event-link">
                        <div class="event-name">{city}</div>
                        <div class="hedge-pair">{title1} / {title2}</div>
                    </a>
                </td>
                <td class="derisk-detail">Sold @ {sold_price}¢</td>
                <td class="{pnl_class}">{pnl_text}</td>
                <td class="status-cell">
                    <span class="hedge-badge derisk">DE-RISKED</span>
                </td>
            </tr>
            """
            continue

        # Track stats for active positions
        if status1 == "filled":
            filled_count += 1
            total_cost += price1
        elif status1 == "pending":
            pending_count += 1

        if status2 == "filled":
            filled_count += 1
            total_cost += price2
        elif status2 == "pending":
            pending_count += 1

        profit_if_win = 100 - total_hedge_cost
        loss_if_miss = total_hedge_cost

        if status1 == "filled" and status2 == "filled":
            total_potential_profit += profit_if_win
        elif (status1 == "filled" and status2 == "pending") or (status2 == "filled" and status1 == "pending"):
            # Partial fill - track at-risk amount
            at_risk_cost += price1 if status1 == "filled" else price2

        ticker1 = pos1.get("ticker", "")
        ticker2 = pos2.get("ticker", "")
        # Build Kalshi URL: /markets/{series}/{slug}/{event_ticker}
        import re
        event_ticker = re.sub(r'-[BT][\d.-]+$', '', ticker1)
        series_lower = series.lower()
        # Build slug from city and type
        slug_map = {
            "KXHIGHDEN": "highest-temperature-in-denver",
            "KXHIGHAUS": "highest-temperature-in-austin",
            "KXHIGHNY": "highest-temperature-in-new-york",
            "KXHIGHMIA": "highest-temperature-in-miami",
            "KXHIGHTNOLA": "highest-temperature-in-new-orleans",
            "KXHIGHLAX": "highest-temperature-in-los-angeles",
            "KXHIGHCHI": "highest-temperature-in-chicago",
            "KXHIGHPHIL": "highest-temperature-in-philadelphia",
            "KXHIGHTSEA": "highest-temperature-in-seattle",
            "KXHIGHTSFO": "highest-temperature-in-san-francisco",
            "KXHIGHTDC": "highest-temperature-in-washington-dc",
            "KXHIGHTLV": "highest-temperature-in-las-vegas",
            "KXLOWTNYC": "lowest-temperature-in-new-york",
            "KXLOWTLAX": "lowest-temperature-in-los-angeles",
            "KXLOWTDEN": "lowest-temperature-in-denver",
            "KXLOWTCHI": "lowest-temperature-in-chicago",
            "KXLOWTMIA": "lowest-temperature-in-miami",
            "KXLOWTPHIL": "lowest-temperature-in-philadelphia",
            "KXLOWTAUS": "lowest-temperature-in-austin",
        }
        slug = slug_map.get(series, "temperature")
        event_url = f"https://kalshi.com/markets/{series_lower}/{slug}/{event_ticker.lower()}"

        # Status badge class
        def status_class(s):
            if s == "filled":
                return "status-filled"
            elif s == "pending":
                return "status-pending"
            else:
                return "status-partial"

        # Determine overall hedge status
        if status1 == "filled" and status2 == "filled":
            hedge_status = "filled"
            hedge_status_text = "READY"
        elif status1 == "pending" and status2 == "pending":
            hedge_status = "pending"
            hedge_status_text = "WAITING"
        else:
            hedge_status = "partial"
            hedge_status_text = "AT RISK"

        # Check for 99% market agreement (effectively settled)
        hedge_won = None  # True if won, False if lost, None if not settled
        winning_bucket = None
        market_closed = False

        # Check if market has closed by parsing the ticker date
        try:
            parts = ticker1.split("-")
            if len(parts) >= 2:
                date_part = parts[1]  # e.g., "26JAN27"
                day = int(date_part[5:7])
                month_str = date_part[2:5].upper()
                month_map = {
                    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
                    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
                }
                month = month_map.get(month_str, 1)
                now = datetime.now()
                year = now.year
                if month == 1 and now.month == 12:
                    year += 1
                from datetime import date
                market_date = date(year, month, day)
                market_closed = now.date() > market_date
        except (ValueError, IndexError):
            pass

        if status1 == "filled" and status2 == "filled":
            agreement1 = check_market_agreement(ticker1)
            agreement2 = check_market_agreement(ticker2)

            if agreement1.get("agreed") and agreement1.get("winning"):
                hedge_won = True
                winning_bucket = title1
                hedge_status = "won"
                hedge_status_text = "WON"
                won_count += 1
                won_profit += profit_if_win
            elif agreement2.get("agreed") and agreement2.get("winning"):
                hedge_won = True
                winning_bucket = title2
                hedge_status = "won"
                hedge_status_text = "WON"
                won_count += 1
                won_profit += profit_if_win
            elif agreement1.get("agreed") and agreement2.get("agreed"):
                # Both buckets agreed at losing - hedge lost
                if not agreement1.get("winning") and not agreement2.get("winning"):
                    hedge_won = False
                    hedge_status = "lost"
                    hedge_status_text = "LOST"
                    lost_count += 1
                    lost_loss += loss_if_miss
            elif market_closed:
                # Market closed but no winner detected - hedge lost
                hedge_won = False
                hedge_status = "lost"
                hedge_status_text = "LOST"
                lost_count += 1
                lost_loss += loss_if_miss

        # Get reprice count if any
        reprice1 = pos1.get("reprice_count", 0)
        reprice2 = pos2.get("reprice_count", 0)
        reprice_note = ""
        if reprice1 > 0 or reprice2 > 0:
            reprice_note = f'<div class="reprice-note">Repriced {max(reprice1, reprice2)}x</div>'

        # Build outcome note for won/lost hedges
        outcome_note = ""
        if hedge_won is True:
            outcome_note = f'<div class="outcome-note win">Won: {winning_bucket}</div>'
        elif hedge_won is False:
            outcome_note = '<div class="outcome-note loss">Both buckets lost</div>'

        # Show actual P&L for settled hedges
        if hedge_won is True:
            profit_display = f'+{profit_if_win}¢'
            loss_display = '--'
        elif hedge_won is False:
            profit_display = '--'
            loss_display = f'-{loss_if_miss}¢'
        else:
            profit_display = f'+{profit_if_win}¢'
            loss_display = f'-{loss_if_miss}¢'

        # Build sell button (only show for the winning bucket, not for lost hedges)
        sell_btn = ""
        if hedge_won is True:
            # Only show sell for the winning bucket
            if agreement1.get("agreed") and agreement1.get("winning"):
                sell_btn = f'<button class="sell-btn" onclick="sellContract(\'{ticker1}\', \'{key1}\')">Sell</button>'
            elif agreement2.get("agreed") and agreement2.get("winning"):
                sell_btn = f'<button class="sell-btn" onclick="sellContract(\'{ticker2}\', \'{key2}\')">Sell</button>'
        # No sell button for lost hedges or unsettled hedges

        rows_html += f"""
        <tr class="hedge-row {hedge_status}">
            <td class="event-cell">
                <a href="{event_url}" target="_blank" class="event-link">
                    <div class="event-name">{city}</div>
                    <div class="hedge-pair">{title1} / {title2}</div>
                </a>
            </td>
            <td class="ticker-cell">
                <span class="ticker-name">{ticker1}</span>
                <span class="badge {status_class(status1)}">{status1}</span>
                <div class="price">{price1}¢</div>
            </td>
            <td class="ticker-cell">
                <span class="ticker-name">{ticker2}</span>
                <span class="badge {status_class(status2)}">{status2}</span>
                <div class="price">{price2}¢</div>
            </td>
            <td class="cost-cell">{total_hedge_cost}¢</td>
            <td class="profit-cell {'profit' if hedge_won is not False else ''}">{profit_display}</td>
            <td class="loss-cell {'loss' if hedge_won is not True else ''}">{loss_display}</td>
            <td class="status-cell">
                <span class="hedge-badge {hedge_status}">{hedge_status_text}</span>
                {outcome_note}
                {reprice_note}
            </td>
            <td class="actions-cell">
                {sell_btn}
            </td>
        </tr>
        """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Weather Trading Dashboard</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            color: #e4e4e4;
            padding: 20px;
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}

        header {{
            text-align: center;
            padding: 30px 0;
            border-bottom: 1px solid rgba(255,255,255,0.1);
            margin-bottom: 30px;
        }}

        h1 {{
            font-size: 2.5rem;
            background: linear-gradient(90deg, #00d4ff, #7b2cbf);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            margin-bottom: 10px;
        }}

        .subtitle {{
            color: #888;
            font-size: 0.9rem;
        }}

        .refresh-btn {{
            background: linear-gradient(135deg, #00d4ff, #7b2cbf);
            border: none;
            color: #fff;
            padding: 12px 30px;
            font-size: 1rem;
            font-weight: 600;
            border-radius: 8px;
            cursor: pointer;
            margin-top: 15px;
            transition: transform 0.2s, box-shadow 0.2s;
        }}

        .refresh-btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 5px 20px rgba(0, 212, 255, 0.3);
        }}

        .refresh-btn:active {{
            transform: translateY(0);
        }}

        .refresh-btn:disabled {{
            opacity: 0.6;
            cursor: not-allowed;
            transform: none;
        }}

        .header-buttons {{
            display: flex;
            gap: 10px;
            justify-content: center;
            margin-top: 15px;
        }}

        .header-buttons .refresh-btn {{
            margin-top: 0;
        }}

        .save-btn {{
            background: linear-gradient(135deg, #00e676, #00c853);
            border: none;
            color: #fff;
            padding: 12px 30px;
            font-size: 1rem;
            font-weight: 600;
            border-radius: 8px;
            cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s;
        }}

        .save-btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 5px 20px rgba(0, 230, 118, 0.3);
        }}

        .save-btn:disabled {{
            opacity: 0.6;
            cursor: not-allowed;
            transform: none;
        }}

        .reset-btn {{
            background: linear-gradient(135deg, #ff5252, #d32f2f);
            border: none;
            color: #fff;
            padding: 12px 30px;
            font-size: 1rem;
            font-weight: 600;
            border-radius: 8px;
            cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s;
        }}

        .reset-btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 5px 20px rgba(255, 82, 82, 0.3);
        }}

        .reset-btn:disabled {{
            opacity: 0.6;
            cursor: not-allowed;
            transform: none;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}

        .stat-card {{
            background: rgba(255,255,255,0.05);
            border-radius: 12px;
            padding: 20px;
            text-align: center;
            border: 1px solid rgba(255,255,255,0.1);
        }}

        .stat-value {{
            font-size: 2rem;
            font-weight: bold;
            margin-bottom: 5px;
        }}

        .stat-label {{
            color: #888;
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}

        .stat-card.profit .stat-value {{
            color: #00e676;
        }}

        .stat-card.loss .stat-value {{
            color: #ff5252;
        }}

        .stat-card.cost .stat-value {{
            color: #ffd740;
        }}

        .stat-card.filled .stat-value {{
            color: #00d4ff;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            background: rgba(255,255,255,0.03);
            border-radius: 12px;
            overflow: hidden;
        }}

        th {{
            background: rgba(0,0,0,0.3);
            padding: 15px;
            text-align: left;
            font-weight: 600;
            text-transform: uppercase;
            font-size: 0.75rem;
            letter-spacing: 1px;
            color: #888;
        }}

        td {{
            padding: 15px;
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }}

        tr:hover {{
            background: rgba(255,255,255,0.05);
        }}

        tfoot .total-row {{
            background: rgba(255,255,255,0.08);
            border-top: 2px solid rgba(255,255,255,0.2);
        }}

        tfoot .total-row:hover {{
            background: rgba(255,255,255,0.08);
        }}

        .event-name {{
            font-weight: 600;
            font-size: 1.1rem;
            color: #fff;
        }}

        .hedge-pair {{
            color: #888;
            font-size: 0.85rem;
            margin-top: 4px;
        }}

        .event-link {{
            color: inherit;
            text-decoration: none;
            display: block;
        }}

        .event-link:hover .event-name {{
            color: #00d4ff;
        }}

        .ticker-name {{
            color: #888;
            font-family: monospace;
            font-size: 0.85rem;
        }}

        .price {{
            color: #888;
            font-size: 0.85rem;
            margin-top: 4px;
        }}

        .badge {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 0.7rem;
            font-weight: 600;
            text-transform: uppercase;
            margin-left: 8px;
        }}

        .status-filled {{
            background: rgba(0, 230, 118, 0.2);
            color: #00e676;
        }}

        .status-pending {{
            background: rgba(255, 215, 64, 0.2);
            color: #ffd740;
        }}

        .status-partial {{
            background: rgba(255, 152, 0, 0.2);
            color: #ff9800;
        }}

        .hedge-badge {{
            display: inline-block;
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 0.75rem;
            font-weight: 700;
            text-transform: uppercase;
        }}

        .hedge-badge.filled {{
            background: linear-gradient(135deg, #00e676, #00c853);
            color: #000;
        }}

        .hedge-badge.pending {{
            background: rgba(255, 215, 64, 0.3);
            color: #ffd740;
        }}

        .hedge-badge.partial {{
            background: rgba(255, 152, 0, 0.3);
            color: #ff9800;
        }}

        .profit {{
            color: #00e676;
            font-weight: 600;
        }}

        .loss {{
            color: #ff5252;
            font-weight: 600;
        }}

        .cost-cell {{
            font-weight: 600;
            color: #ffd740;
        }}

        .actions-cell {{
            text-align: center;
        }}

        .sell-btn {{
            background: linear-gradient(135deg, #ff5252, #d32f2f);
            border: none;
            color: #fff;
            padding: 6px 12px;
            font-size: 0.75rem;
            font-weight: 600;
            border-radius: 4px;
            cursor: pointer;
            margin: 2px;
            transition: transform 0.2s, box-shadow 0.2s;
        }}

        .sell-btn:hover {{
            transform: translateY(-1px);
            box-shadow: 0 3px 10px rgba(255, 82, 82, 0.3);
        }}

        .sell-btn:active {{
            transform: translateY(0);
        }}

        .sell-btn:disabled {{
            opacity: 0.5;
            cursor: not-allowed;
            transform: none;
        }}

        .refresh-note {{
            text-align: center;
            color: #666;
            font-size: 0.8rem;
            margin-top: 20px;
        }}

        .hedge-row.filled {{
            background: rgba(0, 230, 118, 0.03);
        }}

        .hedge-row.pending {{
            background: rgba(255, 215, 64, 0.03);
        }}

        .hedge-row.partial {{
            background: rgba(255, 152, 0, 0.05);
            border-left: 3px solid #ff9800;
        }}

        .hedge-row.derisk {{
            background: rgba(156, 39, 176, 0.05);
            opacity: 0.7;
        }}

        .stat-card.atrisk .stat-value {{
            color: #ff9800;
        }}

        .stat-card.derisk .stat-value {{
            color: #9c27b0;
        }}

        .hedge-badge.derisk {{
            background: rgba(156, 39, 176, 0.3);
            color: #ce93d8;
        }}

        .hedge-badge.won {{
            background: linear-gradient(135deg, #00e676, #00c853);
            color: #000;
            animation: pulse-win 2s ease-in-out infinite;
        }}

        .hedge-badge.lost {{
            background: rgba(255, 82, 82, 0.3);
            color: #ff5252;
        }}

        .hedge-row.won {{
            background: rgba(0, 230, 118, 0.08);
            border-left: 3px solid #00e676;
        }}

        .hedge-row.lost {{
            background: rgba(255, 82, 82, 0.05);
            border-left: 3px solid #ff5252;
            opacity: 0.8;
        }}

        .outcome-note {{
            font-size: 0.7rem;
            margin-top: 4px;
        }}

        .outcome-note.win {{
            color: #00e676;
        }}

        .outcome-note.loss {{
            color: #ff5252;
        }}

        @keyframes pulse-win {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.7; }}
        }}

        .section-title {{
            margin-top: 40px;
            margin-bottom: 15px;
            color: #888;
            font-size: 1rem;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}

        .derisk-table {{
            width: 100%;
            border-collapse: collapse;
            background: rgba(156, 39, 176, 0.03);
            border-radius: 12px;
            overflow: hidden;
            margin-bottom: 20px;
        }}

        .derisk-detail {{
            color: #888;
        }}

        .reprice-note {{
            font-size: 0.7rem;
            color: #ff9800;
            margin-top: 4px;
        }}

        .forecast-cell {{
            text-align: center;
        }}

        .forecast-temp {{
            font-size: 1.1rem;
            font-weight: 600;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
        }}

        .forecast-icon {{
            font-size: 0.9rem;
        }}

        .forecast-note {{
            font-size: 0.7rem;
            color: #888;
            margin-top: 4px;
        }}

        .forecast-good {{
            color: #00e676;
        }}

        .forecast-good .forecast-icon {{
            color: #00e676;
        }}

        .forecast-ok {{
            color: #ffd740;
        }}

        .forecast-ok .forecast-icon {{
            color: #ffd740;
        }}

        .forecast-close {{
            color: #ff9800;
        }}

        .forecast-close .forecast-icon {{
            color: #ff9800;
        }}

        .forecast-bad {{
            color: #ff5252;
        }}

        .forecast-bad .forecast-icon {{
            color: #ff5252;
        }}

        .forecast-unknown {{
            color: #666;
        }}

        .forecast-settling {{
            color: #ce93d8;
            font-size: 0.85rem;
        }}

        @keyframes spin {{
            0% {{ transform: rotate(0deg); }}
            100% {{ transform: rotate(360deg); }}
        }}

        .spinner {{
            display: inline-block;
            width: 16px;
            height: 16px;
            border: 2px solid #fff;
            border-top-color: transparent;
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
            margin-right: 8px;
            vertical-align: middle;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Weather Trading Dashboard</h1>
            <p class="subtitle">Hedged YES Strategy | Updated: <span id="update-time">{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</span></p>
            <div class="header-buttons">
                <button class="refresh-btn" onclick="refreshData()">Refresh from Kalshi</button>
                <button class="save-btn" onclick="saveResults()">Save Results</button>
                <button class="reset-btn" onclick="resetSession()">Reset</button>
            </div>
        </header>

        <div class="stats-grid">
            <div class="stat-card filled">
                <div class="stat-value"><span style="color: #00d4ff;">{filled_count}</span> / <span style="color: #ffd740;">{pending_count}</span></div>
                <div class="stat-label">Filled / Pending</div>
            </div>
            <div class="stat-card cost">
                <div class="stat-value">${total_cost/100:.2f}</div>
                <div class="stat-label">Total Invested</div>
            </div>
            <div class="stat-card">
                <div class="stat-value"><span style="color: #00e676;">+${total_potential_profit/100:.2f}</span> / <span style="color: #ff5252;">-${total_cost/100:.2f}</span></div>
                <div class="stat-label">If Win / If Lose</div>
            </div>
            {"" if at_risk_cost == 0 else f'''<div class="stat-card atrisk">
                <div class="stat-value">${at_risk_cost/100:.2f}</div>
                <div class="stat-label">At Risk (Partial Fills)</div>
            </div>'''}
            {"" if derisk_count == 0 else f'''<div class="stat-card {"profit" if derisk_loss >= 0 else "derisk"}">
                <div class="stat-value">{("+" if derisk_loss >= 0 else "-")}${abs(derisk_loss)/100:.2f}</div>
                <div class="stat-label">De-Risk {"Profit" if derisk_loss >= 0 else "Loss"} ({derisk_count})</div>
            </div>'''}
            {"" if won_count > 0 or lost_count > 0 else ""}{"" if won_count == 0 and lost_count == 0 else f'''<div class="stat-card">
                <div class="stat-value"><span style="color: #00e676;">+${won_profit/100:.2f}</span> / <span style="color: #ff5252;">-${lost_loss/100:.2f}</span></div>
                <div class="stat-label">Won ({won_count}) / Lost ({lost_count})</div>
            </div>'''}
        </div>

        <table>
            <thead>
                <tr>
                    <th>Event</th>
                    <th>Bucket 1</th>
                    <th>Bucket 2</th>
                    <th>Total Cost</th>
                    <th>Profit</th>
                    <th>Loss</th>
                    <th>Status</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
            <tfoot>
                <tr class="total-row">
                    <td colspan="4" style="text-align: right; font-weight: bold;">Net Profit:</td>
                    <td colspan="4" style="font-weight: bold; color: {'#00e676' if (won_profit - lost_loss) >= 0 else '#ff5252'};">{'+' if (won_profit - lost_loss) >= 0 else ''}{(won_profit - lost_loss)}¢</td>
                </tr>
            </tfoot>
        </table>

        <p class="refresh-note">Each hedge wins if temp lands in EITHER bucket</p>

        {"" if derisk_count == 0 else f'''
        <h2 class="section-title">De-Risked Positions</h2>
        <table class="derisk-table">
            <thead>
                <tr>
                    <th>Event</th>
                    <th>Exit Price</th>
                    <th>Loss</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>
                {derisk_rows_html}
            </tbody>
            <tfoot>
                <tr class="total-row">
                    <td colspan="2" style="text-align: right; font-weight: bold;">Net P&L:</td>
                    <td colspan="2" style="font-weight: bold; color: {"#00e676" if derisk_loss >= 0 else "#ff5252"};">{"+" if derisk_loss >= 0 else ""}{derisk_loss}¢</td>
                </tr>
            </tfoot>
        </table>
        '''}

        {"" if (won_count == 0 and lost_count == 0 and derisk_count == 0) else f'''
        <div class="total-profit-card" style="margin-top: 20px; padding: 20px; background: rgba(255,255,255,0.05); border-radius: 12px; text-align: center; border: 1px solid rgba(255,255,255,0.1);">
            <div style="font-size: 0.85rem; color: #888; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px;">Combined Net Profit</div>
            <div style="font-size: 2rem; font-weight: bold; color: {"#00e676" if (won_profit - lost_loss + derisk_loss) >= 0 else "#ff5252"};">{"+" if (won_profit - lost_loss + derisk_loss) >= 0 else ""}${abs(won_profit - lost_loss + derisk_loss)/100:.2f}</div>
            <div style="font-size: 0.8rem; color: #666; margin-top: 8px;">Positions: {"+" if (won_profit - lost_loss) >= 0 else ""}${(won_profit - lost_loss)/100:.2f} | De-Risk: {"+" if derisk_loss >= 0 else ""}${derisk_loss/100:.2f}</div>
        </div>
        '''}
    </div>

    <script>
        function refreshData() {{
            const btn = document.querySelector('.refresh-btn');
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner"></span>Syncing...';

            fetch('/refresh', {{ method: 'POST' }})
                .then(response => response.json())
                .then(data => {{
                    if (data.success) {{
                        window.location.reload();
                    }} else {{
                        alert('Refresh failed: ' + data.error);
                        btn.disabled = false;
                        btn.textContent = 'Refresh from Kalshi';
                    }}
                }})
                .catch(err => {{
                    alert('Error: ' + err);
                    btn.disabled = false;
                    btn.textContent = 'Refresh from Kalshi';
                }});
        }}

        function sellContract(ticker, posKey) {{
            const btn = event.target;
            btn.disabled = true;
            btn.textContent = 'Selling...';

            fetch('/sell', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ ticker: ticker, pos_key: posKey }})
            }})
                .then(response => response.json())
                .then(data => {{
                    if (data.success) {{
                        alert(data.message + '\\nP&L: ' + data.pnl + '¢');
                        window.location.reload();
                    }} else {{
                        alert('Sell failed: ' + data.message);
                        btn.disabled = false;
                        btn.textContent = 'Sell';
                    }}
                }})
                .catch(err => {{
                    alert('Error: ' + err);
                    btn.disabled = false;
                    btn.textContent = 'Sell';
                }});
        }}

        function saveResults() {{
            const btn = document.querySelector('.save-btn');
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner"></span>Saving...';

            fetch('/save-results', {{ method: 'POST' }})
                .then(response => response.json())
                .then(data => {{
                    if (data.success) {{
                        // Open results page in new tab
                        window.open('/results?date=' + data.date, '_blank');
                        btn.disabled = false;
                        btn.textContent = 'Save Results';
                    }} else {{
                        alert('Save failed: ' + data.error);
                        btn.disabled = false;
                        btn.textContent = 'Save Results';
                    }}
                }})
                .catch(err => {{
                    alert('Error: ' + err);
                    btn.disabled = false;
                    btn.textContent = 'Save Results';
                }});
        }}

        function resetSession() {{
            if (!confirm('Are you sure you want to reset? This will clear all positions and start fresh.')) {{
                return;
            }}

            const btn = document.querySelector('.reset-btn');
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner"></span>Resetting...';

            fetch('/reset', {{ method: 'POST' }})
                .then(response => response.json())
                .then(data => {{
                    if (data.success) {{
                        alert('Session reset successfully!');
                        window.location.reload();
                    }} else {{
                        alert('Reset failed: ' + data.error);
                        btn.disabled = false;
                        btn.textContent = 'Reset';
                    }}
                }})
                .catch(err => {{
                    alert('Error: ' + err);
                    btn.disabled = false;
                    btn.textContent = 'Reset';
                }});
        }}
    </script>
</body>
</html>"""
    return html


def save_results():
    """
    Save current session results to trade_history.json.
    Imports all positions and updates the summary.
    """
    from trade_history import import_from_positions, load_history, _update_summary, save_history

    # Import current positions to trade history
    import_from_positions()

    # Reload and update summary
    history = load_history()
    _update_summary(history)
    save_history(history)

    # Get the market date from current positions
    positions = load_positions()
    market_date = None
    for pos in positions.values():
        ticker = pos.get("ticker", "")
        parts = ticker.split("-")
        if len(parts) >= 2:
            date_part = parts[1]
            try:
                day = int(date_part[5:7])
                month_str = date_part[2:5].upper()
                month_map = {
                    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
                    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
                }
                month = month_map.get(month_str, 1)
                year = datetime.now().year
                if month == 1 and datetime.now().month == 12:
                    year += 1
                market_date = f"{year}-{month:02d}-{day:02d}"
                break
            except (ValueError, IndexError):
                pass

    if not market_date:
        market_date = datetime.now().strftime("%Y-%m-%d")

    return {"success": True, "date": market_date}


def reset_session():
    """
    Reset the session by clearing positions.json.
    Does NOT clear trade_history.json (that's permanent storage).
    """
    from monitor import save_positions

    # Clear positions.json
    save_positions({})

    # Clear forecast cache
    global FORECAST_CACHE
    FORECAST_CACHE = {}

    return {"success": True}


def generate_results_html(market_date=None):
    """Generate HTML page showing results for a specific date."""
    from trade_history import load_history

    history = load_history()
    summary = history.get("summary", {})
    by_date = summary.get("by_date", {})

    # If no date specified, use the most recent
    if not market_date and by_date:
        market_date = sorted(by_date.keys())[-1]

    # Get trades for this date
    trades = [t for t in history.get("trades", []) if t.get("market_date") == market_date]
    day_summary = by_date.get(market_date, {})

    # Build rows for trades table
    rows_html = ""
    for trade in trades:
        city = trade.get("city", "Unknown")
        temp_type = trade.get("temp_type", "")
        outcome = trade.get("outcome", "open")
        pnl = trade.get("pnl")
        total_cost = trade.get("total_cost", 0)

        leg1 = trade.get("leg1", {})
        leg2 = trade.get("leg2", {})

        outcome_class = {
            "win": "won",
            "loss": "lost",
            "derisked": "derisk",
            "open": "pending"
        }.get(outcome, "")

        outcome_badge = {
            "win": "WON",
            "loss": "LOST",
            "derisked": "DE-RISKED",
            "open": "OPEN"
        }.get(outcome, "?")

        pnl_display = f"+{pnl}¢" if pnl and pnl >= 0 else f"{pnl}¢" if pnl else "--"
        pnl_color = "#00e676" if pnl and pnl >= 0 else "#ff5252" if pnl else "#888"

        rows_html += f"""
        <tr class="trade-row {outcome_class}">
            <td>{city} {temp_type}</td>
            <td>{leg1.get('title', '?')} @ {leg1.get('order_price', '?')}¢</td>
            <td>{leg2.get('title', '?')} @ {leg2.get('order_price', '?')}¢</td>
            <td>{total_cost}¢</td>
            <td style="color: {pnl_color}; font-weight: bold;">{pnl_display}</td>
            <td><span class="outcome-badge {outcome_class}">{outcome_badge}</span></td>
        </tr>
        """

    # Summary stats
    wins = day_summary.get("wins", 0)
    losses = day_summary.get("losses", 0)
    derisked = day_summary.get("derisked", 0)
    open_count = day_summary.get("open", 0)
    won_profit = day_summary.get("won_profit", 0)
    lost_loss = day_summary.get("lost_loss", 0)
    derisk_pnl = day_summary.get("derisk_pnl", 0)
    net_profit = day_summary.get("net_profit", 0)
    total_invested = day_summary.get("total_invested", 0)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Results - {market_date}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            color: #e0e0e0;
            padding: 20px;
        }}

        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}

        header {{
            text-align: center;
            margin-bottom: 30px;
        }}

        h1 {{
            font-size: 2.5rem;
            background: linear-gradient(135deg, #00d4ff, #7b2cbf);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 10px;
        }}

        .date-display {{
            font-size: 1.5rem;
            color: #888;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}

        .stat-card {{
            background: rgba(255,255,255,0.05);
            border-radius: 12px;
            padding: 20px;
            text-align: center;
            border: 1px solid rgba(255,255,255,0.1);
        }}

        .stat-value {{
            font-size: 2rem;
            font-weight: 700;
            margin-bottom: 5px;
        }}

        .stat-label {{
            color: #888;
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}

        .profit {{ color: #00e676; }}
        .loss {{ color: #ff5252; }}

        table {{
            width: 100%;
            border-collapse: collapse;
            background: rgba(255,255,255,0.03);
            border-radius: 12px;
            overflow: hidden;
            margin-bottom: 20px;
        }}

        th {{
            background: rgba(255,255,255,0.1);
            padding: 15px;
            text-align: left;
            font-weight: 600;
            text-transform: uppercase;
            font-size: 0.85rem;
            letter-spacing: 1px;
        }}

        td {{
            padding: 15px;
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }}

        tr:hover {{
            background: rgba(255,255,255,0.05);
        }}

        .outcome-badge {{
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.75rem;
            font-weight: 600;
        }}

        .outcome-badge.won {{
            background: rgba(0, 230, 118, 0.2);
            color: #00e676;
        }}

        .outcome-badge.lost {{
            background: rgba(255, 82, 82, 0.2);
            color: #ff5252;
        }}

        .outcome-badge.derisk {{
            background: rgba(255, 152, 0, 0.2);
            color: #ff9800;
        }}

        .outcome-badge.pending {{
            background: rgba(255, 215, 64, 0.2);
            color: #ffd740;
        }}

        .net-profit-card {{
            background: rgba(255,255,255,0.05);
            border-radius: 12px;
            padding: 30px;
            text-align: center;
            border: 2px solid {"#00e676" if net_profit >= 0 else "#ff5252"};
            margin-top: 20px;
        }}

        .net-profit-value {{
            font-size: 3rem;
            font-weight: 700;
            color: {"#00e676" if net_profit >= 0 else "#ff5252"};
        }}

        .back-link {{
            display: inline-block;
            margin-top: 20px;
            color: #00d4ff;
            text-decoration: none;
        }}

        .back-link:hover {{
            text-decoration: underline;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Trading Results</h1>
            <div class="date-display">{market_date}</div>
        </header>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-value">{day_summary.get('total_trades', len(trades))}</div>
                <div class="stat-label">Total Trades</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${total_invested/100:.2f}</div>
                <div class="stat-label">Total Invested</div>
            </div>
            <div class="stat-card">
                <div class="stat-value profit">+${won_profit/100:.2f}</div>
                <div class="stat-label">Won Profit ({wins})</div>
            </div>
            <div class="stat-card">
                <div class="stat-value loss">-${lost_loss/100:.2f}</div>
                <div class="stat-label">Lost ({losses})</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" style="color: {'#00e676' if derisk_pnl >= 0 else '#ff5252'};">{"+" if derisk_pnl >= 0 else ""}${derisk_pnl/100:.2f}</div>
                <div class="stat-label">De-Risk P&L ({derisked})</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" style="color: #ffd740;">{open_count}</div>
                <div class="stat-label">Still Open</div>
            </div>
        </div>

        <table>
            <thead>
                <tr>
                    <th>Market</th>
                    <th>Leg 1</th>
                    <th>Leg 2</th>
                    <th>Cost</th>
                    <th>P&L</th>
                    <th>Outcome</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>

        <div class="net-profit-card">
            <div class="stat-label" style="margin-bottom: 10px;">Net Profit</div>
            <div class="net-profit-value">{"+" if net_profit >= 0 else ""}${net_profit/100:.2f}</div>
            <div style="margin-top: 10px; color: #888;">
                Won: +${won_profit/100:.2f} | Lost: -${lost_loss/100:.2f} | De-Risk: {"+" if derisk_pnl >= 0 else ""}${derisk_pnl/100:.2f}
            </div>
        </div>

        <div style="text-align: center;">
            <a href="/" class="back-link">Back to Dashboard</a>
        </div>
    </div>
</body>
</html>"""
    return html


class DashboardHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(generate_html().encode())
        elif self.path.startswith("/results"):
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            # Extract date from query string
            from urllib.parse import urlparse, parse_qs
            query = parse_qs(urlparse(self.path).query)
            date = query.get("date", [None])[0]
            self.wfile.write(generate_results_html(date).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/refresh":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            try:
                sync_with_kalshi()
                self.wfile.write(json.dumps({"success": True}).encode())
            except Exception as e:
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode())
        elif self.path == "/sell":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            try:
                content_length = int(self.headers['Content-Length'])
                post_data = json.loads(self.rfile.read(content_length))
                ticker = post_data.get("ticker")
                pos_key = post_data.get("pos_key")
                if not ticker or not pos_key:
                    self.wfile.write(json.dumps({"success": False, "message": "Missing ticker or pos_key"}).encode())
                else:
                    result = sell_contract(ticker, pos_key)
                    self.wfile.write(json.dumps(result).encode())
            except Exception as e:
                self.wfile.write(json.dumps({"success": False, "message": str(e)}).encode())
        elif self.path == "/save-results":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            try:
                result = save_results()
                self.wfile.write(json.dumps(result).encode())
            except Exception as e:
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode())
        elif self.path == "/reset":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            try:
                result = reset_session()
                self.wfile.write(json.dumps(result).encode())
            except Exception as e:
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress logging


def main():
    port = 8080
    server = HTTPServer(("localhost", port), DashboardHandler)
    print(f"Dashboard running at http://localhost:{port}")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
