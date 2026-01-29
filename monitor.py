"""
Position Monitor - Tracks positions for hold-to-settlement strategy

Used by strategy.py for position management.
Tracks pending/partial/filled status of limit orders.
"""

import json
from datetime import datetime
from pathlib import Path

POSITIONS_FILE = Path(__file__).parent / "positions.json"


def load_positions():
    """Load tracked positions from file."""
    try:
        with open(POSITIONS_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_positions(positions):
    """Save tracked positions to file."""
    with open(POSITIONS_FILE, "w") as f:
        json.dump(positions, f, indent=2)


def add_pending_order(ticker, side, limit_price, intended_count, order_id=None):
    """Track a newly placed limit order (may be unfilled or partially filled)."""
    positions = load_positions()
    key = f"{ticker}_{side}"
    positions[key] = {
        "ticker": ticker,
        "side": side,
        "limit_price": int(limit_price),
        "intended_count": int(intended_count),
        "count": 0,
        "order_id": order_id,
        "timestamp": datetime.now().isoformat(),
        "status": "pending",
    }
    save_positions(positions)
    print(f"Tracking PENDING: {intended_count} {side.upper()} LIMIT @ {limit_price}c on {ticker}")


def reconcile_pending_orders(fetch_positions_fn):
    """
    Reconcile tracked pending orders against actual portfolio positions.

    Updates each tracked record:
      - status: pending | partial | filled
      - count: actual filled position count from portfolio
    """
    positions = load_positions()
    if not positions:
        return positions

    try:
        portfolio_positions = fetch_positions_fn() or []
    except Exception as e:
        print(f"Failed to fetch portfolio positions: {e}")
        return positions

    # Build map of ticker -> position count from Kalshi API
    # Note: API doesn't return 'side', just ticker and position count
    pos_map = {}
    for p in portfolio_positions:
        t = p.get("ticker")
        c = p.get("position") or p.get("count") or 0
        if t:
            pos_map[t] = int(c)

    changed = False
    for key, rec in positions.items():
        status = rec.get("status", "filled")
        if status not in ("pending", "partial"):
            continue

        t = rec.get("ticker")
        intended = int(rec.get("intended_count", rec.get("count", 0)) or 0)
        filled = pos_map.get(t, 0)

        if filled <= 0:
            rec["status"] = "pending"
            rec["count"] = 0
        elif filled < intended:
            rec["status"] = "partial"
            rec["count"] = filled
            rec.setdefault("filled_timestamp", datetime.now().isoformat())
        else:
            prev = rec.get("status")
            rec["status"] = "filled"
            rec["count"] = filled
            if prev != "filled":
                rec["filled_timestamp"] = datetime.now().isoformat()

        positions[key] = rec
        changed = True

    if changed:
        save_positions(positions)
    return positions


def print_positions():
    """Print current tracked positions."""
    positions = load_positions()
    if not positions:
        print("No positions tracked.")
        return

    print(f"\n{'='*60}")
    print("TRACKED POSITIONS (hold to settlement)")
    print(f"{'='*60}")

    for key, pos in positions.items():
        status = pos.get("status", "unknown")
        limit_price = pos.get("limit_price", 0)
        count = pos.get("count", 0)
        intended = pos.get("intended_count", count)
        title = pos.get("title", pos.get("ticker", key))

        print(f"\n{title}")
        print(f"  Limit price: {limit_price}¢ | Status: {status} | Filled: {count}/{intended}")

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    from orders import get_temperature_positions

    print("Checking order status with Kalshi API...")
    reconcile_pending_orders(get_temperature_positions)
    print_positions()
