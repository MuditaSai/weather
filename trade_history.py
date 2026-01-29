"""
Trade History Tracker - Records all trades with comprehensive data for backtesting

Tracks for each hedge:
- Order placement timestamps and prices
- Fill timestamps and prices
- Forecast at order time and throughout the day
- Sell/settlement timestamps and prices
- Final P&L and outcome
"""

import json
from datetime import datetime
from pathlib import Path

HISTORY_FILE = Path(__file__).parent / "trade_history.json"


def load_history():
    """Load trade history from file."""
    try:
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {
            "trades": [],
            "summary": {
                "by_date": {},
                "totals": {
                    "total_trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "derisked": 0,
                    "open": 0,
                    "won_profit": 0,
                    "lost_loss": 0,
                    "derisk_pnl": 0,
                    "net_profit": 0,
                    "total_invested": 0,
                }
            }
        }


def save_history(history):
    """Save trade history to file."""
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def extract_market_date(ticker):
    """Extract market date from ticker (e.g., KXHIGHDEN-26JAN27 -> Jan 27, 2026).

    Ticker format is SERIES-DDMMMDD-BUCKET where the last DD is the day (not year).
    """
    parts = ticker.split("-")
    if len(parts) < 2:
        return None
    date_part = parts[1]  # e.g., "26JAN27" means Jan 27
    try:
        # The last 2 digits are the DAY, not the year
        day = int(date_part[5:7])  # Use the second day value
        month_str = date_part[2:5].upper()

        # Infer year from current date (markets are for today/tomorrow)
        now = datetime.now()
        year = now.year

        # Handle year boundary
        month_map = {
            "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
            "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
        }
        month = month_map.get(month_str, 1)
        if month == 1 and now.month == 12:
            year += 1

        return f"{year}-{month:02d}-{day:02d}"
    except (ValueError, IndexError):
        return date_part


def extract_market_date_display(ticker):
    """Extract market date in display format (e.g., Jan 27, 2026)."""
    parts = ticker.split("-")
    if len(parts) < 2:
        return None
    date_part = parts[1]
    try:
        day = int(date_part[5:7])
        month_str = date_part[2:5].upper()
        now = datetime.now()
        year = now.year
        month_map = {
            "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
            "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
        }
        month = month_map.get(month_str, 1)
        if month == 1 and now.month == 12:
            year += 1
        return f"{month_str} {day}, {year}"
    except (ValueError, IndexError):
        return date_part


def extract_city(series):
    """Extract city name from series (e.g., KXHIGHDEN -> Denver)."""
    city_map = {
        "KXHIGHDEN": "Denver",
        "KXLOWTDEN": "Denver",
        "KXHIGHLAX": "Los Angeles",
        "KXLOWTLAX": "Los Angeles",
        "KXHIGHNY": "New York",
        "KXLOWTNYC": "New York",
        "KXHIGHCHI": "Chicago",
        "KXLOWTCHI": "Chicago",
        "KXHIGHTDC": "Washington DC",
        "KXLOWTDC": "Washington DC",
        "KXHIGHMIA": "Miami",
        "KXLOWTMIA": "Miami",
        "KXHIGHTNOLA": "New Orleans",
        "KXLOWTPNOLA": "New Orleans",
        "KXHIGHTLV": "Las Vegas",
        "KXLOWTLV": "Las Vegas",
        "KXHIGHTSFO": "San Francisco",
        "KXLOWTSFO": "San Francisco",
        "KXHIGHAUS": "Austin",
        "KXLOWTAUS": "Austin",
        "KXHIGHPHIL": "Philadelphia",
        "KXLOWTPHIL": "Philadelphia",
        "KXHIGHTSEA": "Seattle",
        "KXLOWTSEA": "Seattle",
    }
    return city_map.get(series, series)


def extract_temp_type(series):
    """Extract temperature type (HIGH or LOW) from series."""
    if "HIGH" in series:
        return "HIGH"
    elif "LOW" in series:
        return "LOW"
    return "UNKNOWN"


def generate_trade_id(series, ticker):
    """Generate unique trade ID from series and ticker date."""
    date_str = extract_market_date(ticker)
    return f"{series}_{date_str}"


def record_hedge_entry(positions_data, leg1_key, leg2_key, forecast_data=None):
    """
    Record a new hedge entry with all relevant data for backtesting.

    Args:
        positions_data: Dict of all positions from positions.json
        leg1_key: Key for first leg
        leg2_key: Key for second leg
        forecast_data: Dict with forecast info at time of order

    Returns:
        The trade ID
    """
    history = load_history()

    leg1 = positions_data.get(leg1_key, {})
    leg2 = positions_data.get(leg2_key, {})

    series = leg1.get("series", "")
    ticker = leg1.get("ticker", "")
    trade_id = generate_trade_id(series, ticker)

    # Check if trade already exists
    existing = next((t for t in history["trades"] if t["id"] == trade_id), None)
    if existing:
        # Update existing trade
        return update_trade(trade_id, positions_data, leg1_key, leg2_key, forecast_data)

    # Calculate costs
    leg1_price = leg1.get("limit_price", 0)
    leg2_price = leg2.get("limit_price", 0)
    total_cost = leg1_price + leg2_price

    trade = {
        "id": trade_id,
        "series": series,
        "city": extract_city(series),
        "temp_type": extract_temp_type(series),
        "market_date": extract_market_date(ticker),
        "market_date_display": extract_market_date_display(ticker),

        # Leg 1 details
        "leg1": {
            "key": leg1_key,
            "ticker": leg1.get("ticker"),
            "title": leg1.get("title"),
            "floor": leg1.get("floor"),
            "cap": leg1.get("cap"),
            "side": leg1.get("side", "yes"),
            "order_timestamp": leg1.get("timestamp"),
            "order_price": leg1_price,
            "order_id": leg1.get("order_id"),
            "fill_timestamp": leg1.get("filled_timestamp"),
            "fill_price": leg1_price,  # For limit orders, fill = order price
            "status": leg1.get("status"),
            "count": leg1.get("count", 1),
            "reprice_count": leg1.get("reprice_count", 0),
        },

        # Leg 2 details
        "leg2": {
            "key": leg2_key,
            "ticker": leg2.get("ticker"),
            "title": leg2.get("title"),
            "floor": leg2.get("floor"),
            "cap": leg2.get("cap"),
            "side": leg2.get("side", "yes"),
            "order_timestamp": leg2.get("timestamp"),
            "order_price": leg2_price,
            "order_id": leg2.get("order_id"),
            "fill_timestamp": leg2.get("filled_timestamp"),
            "fill_price": leg2_price,
            "status": leg2.get("status"),
            "count": leg2.get("count", 1),
            "reprice_count": leg2.get("reprice_count", 0),
        },

        # Cost and P&L
        "total_cost": total_cost,
        "potential_profit": 100 - total_cost,
        "potential_loss": total_cost,

        # Forecast data - track throughout the day
        "forecasts": [],

        # Outcome (to be filled on settlement)
        "outcome": "open",
        "actual_temp": None,
        "winning_bucket": None,
        "pnl": None,

        # Exit data (if sold early or derisked)
        "exit": None,

        # Timestamps
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "settled_at": None,
    }

    # Add initial forecast if provided
    if forecast_data:
        trade["forecasts"].append({
            "timestamp": datetime.now().isoformat(),
            "forecast_temp": forecast_data.get("forecast_temp"),
            "forecast_high": forecast_data.get("forecast_high"),
            "forecast_low": forecast_data.get("forecast_low"),
            "hourly_temps": forecast_data.get("hourly_temps"),
            "min_temp": forecast_data.get("min_temp"),
            "max_temp": forecast_data.get("max_temp"),
            "source": "nws",
        })

    history["trades"].append(trade)
    save_history(history)

    return trade_id


def update_trade(trade_id, positions_data=None, leg1_key=None, leg2_key=None, forecast_data=None):
    """
    Update an existing trade with new data.

    Args:
        trade_id: The trade ID to update
        positions_data: Optional dict of positions
        leg1_key: Optional key for leg1
        leg2_key: Optional key for leg2
        forecast_data: Optional new forecast data

    Returns:
        The trade ID
    """
    history = load_history()

    trade = next((t for t in history["trades"] if t["id"] == trade_id), None)
    if not trade:
        return None

    # Update leg data if provided
    if positions_data:
        if leg1_key:
            leg1 = positions_data.get(leg1_key, {})
            trade["leg1"]["status"] = leg1.get("status", trade["leg1"]["status"])
            trade["leg1"]["fill_timestamp"] = leg1.get("filled_timestamp") or trade["leg1"]["fill_timestamp"]
            trade["leg1"]["reprice_count"] = leg1.get("reprice_count", 0)
            if leg1.get("sold_price"):
                trade["leg1"]["sold_price"] = leg1.get("sold_price")
                trade["leg1"]["sold_timestamp"] = leg1.get("sold_timestamp")

        if leg2_key:
            leg2 = positions_data.get(leg2_key, {})
            trade["leg2"]["status"] = leg2.get("status", trade["leg2"]["status"])
            trade["leg2"]["fill_timestamp"] = leg2.get("filled_timestamp") or trade["leg2"]["fill_timestamp"]
            trade["leg2"]["reprice_count"] = leg2.get("reprice_count", 0)
            if leg2.get("sold_price"):
                trade["leg2"]["sold_price"] = leg2.get("sold_price")
                trade["leg2"]["sold_timestamp"] = leg2.get("sold_timestamp")

    # Add new forecast data if provided
    if forecast_data:
        trade["forecasts"].append({
            "timestamp": datetime.now().isoformat(),
            "forecast_temp": forecast_data.get("forecast_temp"),
            "forecast_high": forecast_data.get("forecast_high"),
            "forecast_low": forecast_data.get("forecast_low"),
            "hourly_temps": forecast_data.get("hourly_temps"),
            "min_temp": forecast_data.get("min_temp"),
            "max_temp": forecast_data.get("max_temp"),
            "source": forecast_data.get("source", "nws"),
        })

    trade["updated_at"] = datetime.now().isoformat()
    save_history(history)

    return trade_id


def record_forecast_update(trade_id, forecast_data):
    """
    Add a new forecast snapshot to a trade.

    Args:
        trade_id: The trade ID
        forecast_data: Dict with forecast info

    Returns:
        True if successful
    """
    history = load_history()

    trade = next((t for t in history["trades"] if t["id"] == trade_id), None)
    if not trade:
        return False

    trade["forecasts"].append({
        "timestamp": datetime.now().isoformat(),
        "forecast_temp": forecast_data.get("forecast_temp"),
        "forecast_high": forecast_data.get("forecast_high"),
        "forecast_low": forecast_data.get("forecast_low"),
        "hourly_temps": forecast_data.get("hourly_temps"),
        "min_temp": forecast_data.get("min_temp"),
        "max_temp": forecast_data.get("max_temp"),
        "source": forecast_data.get("source", "nws"),
    })

    trade["updated_at"] = datetime.now().isoformat()
    save_history(history)
    return True


def record_sale(trade_id, leg_key, sold_price, sold_timestamp=None):
    """
    Record that a leg was sold.

    Args:
        trade_id: The trade ID
        leg_key: Which leg was sold (key from positions.json)
        sold_price: Price it was sold at
        sold_timestamp: When it was sold (defaults to now)
    """
    history = load_history()

    trade = next((t for t in history["trades"] if t["id"] == trade_id), None)
    if not trade:
        return False

    sold_timestamp = sold_timestamp or datetime.now().isoformat()

    # Determine which leg
    if trade["leg1"]["key"] == leg_key:
        leg = trade["leg1"]
    elif trade["leg2"]["key"] == leg_key:
        leg = trade["leg2"]
    else:
        return False

    leg["sold_price"] = sold_price
    leg["sold_timestamp"] = sold_timestamp
    leg["status"] = "sold"

    # Calculate P&L for this leg
    entry_price = leg.get("fill_price") or leg.get("order_price", 0)
    leg["pnl"] = sold_price - entry_price

    trade["updated_at"] = datetime.now().isoformat()
    save_history(history)
    return True


def record_win(trade_id, winning_leg_key, actual_temp=None, sold_price=99):
    """
    Record that a hedge won (one leg settled at ~100c).

    Args:
        trade_id: The trade ID
        winning_leg_key: Key of the winning leg
        actual_temp: Actual temperature (optional)
        sold_price: Price sold at (default 99 for market settlement)
    """
    history = load_history()

    trade = next((t for t in history["trades"] if t["id"] == trade_id), None)
    if not trade:
        return False

    # Determine winning leg
    if trade["leg1"]["key"] == winning_leg_key:
        winning_leg = trade["leg1"]
        winning_bucket = trade["leg1"]["title"]
    elif trade["leg2"]["key"] == winning_leg_key:
        winning_leg = trade["leg2"]
        winning_bucket = trade["leg2"]["title"]
    else:
        return False

    winning_leg["sold_price"] = sold_price
    winning_leg["sold_timestamp"] = datetime.now().isoformat()
    winning_leg["status"] = "won_sold"

    # Calculate P&L: theoretical profit is 100 - total_cost
    pnl = 100 - trade["total_cost"]

    trade["outcome"] = "win"
    trade["actual_temp"] = actual_temp
    trade["winning_bucket"] = winning_bucket
    trade["pnl"] = pnl
    trade["settled_at"] = datetime.now().isoformat()
    trade["updated_at"] = datetime.now().isoformat()

    trade["exit"] = {
        "type": "win",
        "timestamp": datetime.now().isoformat(),
        "sold_price": sold_price,
        "pnl": pnl,
    }

    _update_summary(history)
    save_history(history)
    return True


def record_loss(trade_id, actual_temp=None):
    """
    Record that a hedge lost (both legs settled at 0c).

    Args:
        trade_id: The trade ID
        actual_temp: Actual temperature (optional)
    """
    history = load_history()

    trade = next((t for t in history["trades"] if t["id"] == trade_id), None)
    if not trade:
        return False

    pnl = -trade["total_cost"]

    trade["outcome"] = "loss"
    trade["actual_temp"] = actual_temp
    trade["pnl"] = pnl
    trade["settled_at"] = datetime.now().isoformat()
    trade["updated_at"] = datetime.now().isoformat()

    trade["exit"] = {
        "type": "loss",
        "timestamp": datetime.now().isoformat(),
        "pnl": pnl,
    }

    _update_summary(history)
    save_history(history)
    return True


def record_derisk(trade_id, sold_leg_key, sold_price, cancelled_leg_key):
    """
    Record that a hedge was de-risked (partial fill situation).

    Args:
        trade_id: The trade ID
        sold_leg_key: Key of the leg that was sold
        sold_price: Price it was sold at
        cancelled_leg_key: Key of the leg that was cancelled
    """
    history = load_history()

    trade = next((t for t in history["trades"] if t["id"] == trade_id), None)
    if not trade:
        return False

    # Update sold leg
    if trade["leg1"]["key"] == sold_leg_key:
        sold_leg = trade["leg1"]
        cancelled_leg = trade["leg2"]
    else:
        sold_leg = trade["leg2"]
        cancelled_leg = trade["leg1"]

    entry_price = sold_leg.get("fill_price") or sold_leg.get("order_price", 0)
    pnl = sold_price - entry_price

    sold_leg["sold_price"] = sold_price
    sold_leg["sold_timestamp"] = datetime.now().isoformat()
    sold_leg["status"] = "derisk_sold"
    sold_leg["pnl"] = pnl

    cancelled_leg["status"] = "derisk_cancelled"
    cancelled_leg["cancelled_timestamp"] = datetime.now().isoformat()

    trade["outcome"] = "derisked"
    trade["pnl"] = pnl
    trade["settled_at"] = datetime.now().isoformat()
    trade["updated_at"] = datetime.now().isoformat()

    trade["exit"] = {
        "type": "derisk",
        "timestamp": datetime.now().isoformat(),
        "sold_leg": sold_leg_key,
        "sold_price": sold_price,
        "entry_price": entry_price,
        "pnl": pnl,
    }

    _update_summary(history)
    save_history(history)
    return True


def _update_summary(history):
    """Update the summary statistics, grouped by market date to match dashboard display."""
    trades = history["trades"]

    # Group trades by market_date
    by_date = {}
    for trade in trades:
        market_date = trade.get("market_date", "unknown")
        if market_date not in by_date:
            by_date[market_date] = []
        by_date[market_date].append(trade)

    # Calculate per-date stats matching dashboard calculations
    daily_summaries = {}
    for market_date, date_trades in sorted(by_date.items()):
        won_profit = 0      # Total profit from won hedges (100 - total_cost)
        lost_loss = 0       # Total loss from lost hedges (total_cost)
        derisk_pnl = 0      # Net P&L from de-risked positions

        wins = 0
        losses = 0
        derisked = 0
        open_count = 0

        for trade in date_trades:
            outcome = trade.get("outcome", "open")

            if outcome == "win":
                wins += 1
                # Profit = 100 - total_cost (theoretical profit)
                won_profit += 100 - trade.get("total_cost", 0)
            elif outcome == "loss":
                losses += 1
                # Loss = total_cost
                lost_loss += trade.get("total_cost", 0)
            elif outcome == "derisked":
                derisked += 1
                # P&L from exit (can be positive or negative)
                derisk_pnl += trade.get("pnl", 0) or 0
            else:
                open_count += 1

        # Net profit matches dashboard: won_profit - lost_loss + derisk_pnl
        net_profit = won_profit - lost_loss + derisk_pnl

        daily_summaries[market_date] = {
            "wins": wins,
            "losses": losses,
            "derisked": derisked,
            "open": open_count,
            "total_trades": len(date_trades),
            "won_profit": won_profit,
            "lost_loss": lost_loss,
            "derisk_pnl": derisk_pnl,
            "net_profit": net_profit,
            "total_invested": sum(t.get("total_cost", 0) for t in date_trades),
        }

    # Also calculate overall totals
    total_won_profit = sum(d["won_profit"] for d in daily_summaries.values())
    total_lost_loss = sum(d["lost_loss"] for d in daily_summaries.values())
    total_derisk_pnl = sum(d["derisk_pnl"] for d in daily_summaries.values())

    history["summary"] = {
        "by_date": daily_summaries,
        "totals": {
            "total_trades": len(trades),
            "wins": sum(1 for t in trades if t.get("outcome") == "win"),
            "losses": sum(1 for t in trades if t.get("outcome") == "loss"),
            "derisked": sum(1 for t in trades if t.get("outcome") == "derisked"),
            "open": sum(1 for t in trades if t.get("outcome") == "open"),
            "won_profit": total_won_profit,
            "lost_loss": total_lost_loss,
            "derisk_pnl": total_derisk_pnl,
            "net_profit": total_won_profit - total_lost_loss + total_derisk_pnl,
            "total_invested": sum(t.get("total_cost", 0) for t in trades),
        }
    }


def import_from_positions(positions_file="positions.json"):
    """
    Import all existing trades from positions.json into trade history.
    Groups positions by series and market date to form hedges.
    """
    from config import MARKETS
    from nws_forecast import get_forecast_for_market
    from datetime import date

    positions_path = Path(__file__).parent / positions_file
    try:
        with open(positions_path, "r") as f:
            positions = json.load(f)
    except FileNotFoundError:
        print(f"Positions file not found: {positions_path}")
        return

    # Group positions by series and date
    hedges = {}
    for key, pos in positions.items():
        series = pos.get("series", "")
        ticker = pos.get("ticker", "")
        if not series or not ticker:
            continue

        # Extract date from ticker
        parts = ticker.split("-")
        if len(parts) < 2:
            continue
        date_part = parts[1]

        hedge_key = f"{series}_{date_part}"
        if hedge_key not in hedges:
            hedges[hedge_key] = []
        hedges[hedge_key].append((key, pos))

    # Process each hedge pair
    imported = 0
    for hedge_key, legs in hedges.items():
        if len(legs) != 2:
            continue  # Skip incomplete hedges

        leg1_key, leg1 = legs[0]
        leg2_key, leg2 = legs[1]

        series = leg1.get("series", "")
        ticker = leg1.get("ticker", "")
        trade_id = generate_trade_id(series, ticker)

        # Try to get forecast for the market date
        forecast_data = None
        try:
            market_date = extract_market_date(ticker)
            if market_date:
                year, month, day = map(int, market_date.split("-"))
                target_date = date(year, month, day)
                forecast = get_forecast_for_market(series, target_date)
                if forecast:
                    forecast_data = forecast
        except Exception as e:
            print(f"Could not get forecast for {series}: {e}")

        # Record the hedge entry
        record_hedge_entry(positions, leg1_key, leg2_key, forecast_data)

        # Determine outcome based on status
        status1 = leg1.get("status", "")
        status2 = leg2.get("status", "")

        if status1 in ("won_sold", "sold") or status2 in ("won_sold", "sold"):
            # This was a win
            winning_key = leg1_key if status1 in ("won_sold", "sold") else leg2_key
            winning_pos = leg1 if status1 in ("won_sold", "sold") else leg2
            sold_price = winning_pos.get("sold_price", 99)
            record_win(trade_id, winning_key, sold_price=sold_price)

        elif status1 == "derisk_sold" or status2 == "derisk_sold":
            # This was a derisk
            if status1 == "derisk_sold":
                sold_key = leg1_key
                sold_price = leg1.get("sold_price", 0)
                cancelled_key = leg2_key
            else:
                sold_key = leg2_key
                sold_price = leg2.get("sold_price", 0)
                cancelled_key = leg1_key
            record_derisk(trade_id, sold_key, sold_price, cancelled_key)

        imported += 1

    print(f"Imported {imported} hedges from positions.json")


def get_trade_by_id(trade_id):
    """Get a specific trade by ID."""
    history = load_history()
    return next((t for t in history["trades"] if t["id"] == trade_id), None)


def get_trades_by_date(market_date):
    """Get all trades for a specific market date."""
    history = load_history()
    return [t for t in history["trades"] if t.get("market_date") == market_date]


def get_trades_by_series(series):
    """Get all trades for a specific series."""
    history = load_history()
    return [t for t in history["trades"] if t.get("series") == series]


def get_open_trades():
    """Get all trades that haven't settled yet."""
    history = load_history()
    return [t for t in history["trades"] if t.get("outcome") == "open"]


def print_history():
    """Print trade history in a readable format."""
    history = load_history()

    if not history["trades"]:
        print("No trades recorded yet.")
        return

    print(f"\n{'='*80}")
    print("TRADE HISTORY")
    print(f"{'='*80}")

    # Group by date
    by_date = {}
    for trade in history["trades"]:
        date = trade.get("market_date_display") or trade.get("market_date", "Unknown")
        if date not in by_date:
            by_date[date] = []
        by_date[date].append(trade)

    for date in sorted(by_date.keys()):
        print(f"\n--- {date} ---")
        for trade in by_date[date]:
            city = trade.get("city", "Unknown")
            temp_type = trade.get("temp_type", "")
            outcome = trade.get("outcome", "open")
            pnl = trade.get("pnl")
            total_cost = trade.get("total_cost", 0)

            leg1 = trade.get("leg1", {})
            leg2 = trade.get("leg2", {})

            pnl_str = f"+{pnl}c" if pnl and pnl >= 0 else f"{pnl}c" if pnl else "--"
            outcome_symbol = {
                "win": "WIN",
                "loss": "LOSS",
                "derisked": "DERISK",
                "open": "OPEN"
            }.get(outcome, "?")

            print(f"\n  {city} {temp_type}")
            print(f"    Leg 1: {leg1.get('title', '?')} @ {leg1.get('order_price', '?')}c")
            print(f"           Ordered: {leg1.get('order_timestamp', '?')[:19] if leg1.get('order_timestamp') else '?'}")
            print(f"           Filled:  {leg1.get('fill_timestamp', '?')[:19] if leg1.get('fill_timestamp') else 'pending'}")
            print(f"    Leg 2: {leg2.get('title', '?')} @ {leg2.get('order_price', '?')}c")
            print(f"           Ordered: {leg2.get('order_timestamp', '?')[:19] if leg2.get('order_timestamp') else '?'}")
            print(f"           Filled:  {leg2.get('fill_timestamp', '?')[:19] if leg2.get('fill_timestamp') else 'pending'}")
            print(f"    Total cost: {total_cost}c | Outcome: {outcome_symbol} | P&L: {pnl_str}")

            # Show forecast history
            if trade.get("forecasts"):
                latest = trade["forecasts"][-1]
                print(f"    Latest forecast: {latest.get('forecast_temp', '?')}°F at {latest.get('timestamp', '?')[:19]}")

            if trade.get("actual_temp") is not None:
                print(f"    Actual temp: {trade['actual_temp']}°F")

            if trade.get("winning_bucket"):
                print(f"    Winner: {trade['winning_bucket']}")

    # Summary - per date (matching dashboard)
    summary = history.get("summary", {})
    by_date_summary = summary.get("by_date", {})
    totals = summary.get("totals", {})

    print(f"\n{'='*80}")
    print("SUMMARY BY DATE (matches dashboard)")
    print(f"{'='*80}")

    for market_date in sorted(by_date_summary.keys()):
        day_stats = by_date_summary[market_date]
        wins = day_stats.get("wins", 0)
        losses = day_stats.get("losses", 0)
        derisked = day_stats.get("derisked", 0)
        open_count = day_stats.get("open", 0)
        won_profit = day_stats.get("won_profit", 0)
        lost_loss = day_stats.get("lost_loss", 0)
        derisk_pnl = day_stats.get("derisk_pnl", 0)
        net_profit = day_stats.get("net_profit", 0)

        pnl_sign = "+" if net_profit >= 0 else ""
        print(f"\n  {market_date}:")
        print(f"    Trades: {day_stats.get('total_trades', 0)} | Wins: {wins} | Losses: {losses} | Derisked: {derisked} | Open: {open_count}")
        print(f"    Won profit: +{won_profit}c | Lost: -{lost_loss}c | De-risk: {'+' if derisk_pnl >= 0 else ''}{derisk_pnl}c")
        print(f"    Net P&L: {pnl_sign}{net_profit}c (${net_profit/100:.2f})")

    # Overall totals
    print(f"\n{'='*80}")
    print("OVERALL TOTALS")
    print(f"{'='*80}")
    print(f"  Total trades: {totals.get('total_trades', len(history['trades']))}")
    print(f"  Wins: {totals.get('wins', 0)} | Losses: {totals.get('losses', 0)} | Derisked: {totals.get('derisked', 0)} | Open: {totals.get('open', 0)}")
    print(f"  Won profit: +{totals.get('won_profit', 0)}c | Lost: -{totals.get('lost_loss', 0)}c | De-risk: {'+' if totals.get('derisk_pnl', 0) >= 0 else ''}{totals.get('derisk_pnl', 0)}c")
    net_pnl = totals.get("net_profit", 0)
    pnl_sign = "+" if net_pnl >= 0 else ""
    print(f"  Net P&L: {pnl_sign}{net_pnl}c (${net_pnl/100:.2f})")
    print(f"{'='*80}\n")


def export_for_backtest(output_file="backtest_data.json"):
    """
    Export trade history in a format optimized for backtesting.

    Includes all timestamps, prices, and forecast snapshots.
    """
    history = load_history()

    backtest_data = {
        "exported_at": datetime.now().isoformat(),
        "trades": history["trades"],
        "summary": history["summary"],
    }

    output_path = Path(__file__).parent / output_file
    with open(output_path, "w") as f:
        json.dump(backtest_data, f, indent=2)

    print(f"Exported {len(history['trades'])} trades to {output_path}")
    return str(output_path)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        if sys.argv[1] == "import":
            import_from_positions()
        elif sys.argv[1] == "export":
            export_for_backtest()
        elif sys.argv[1] == "open":
            trades = get_open_trades()
            print(f"\n{len(trades)} open trade(s):")
            for t in trades:
                print(f"  {t['id']}: {t['city']} {t['temp_type']} - {t['total_cost']}c")

    print_history()
