import requests
from config import API_BASE
from auth import get_auth_headers


def place_limit_order(ticker, side, action, count, price):
    """
    Place a limit order on Kalshi.

    Args:
        ticker: Market ticker (e.g., "KXHIGHTDC-26JAN19-B38")
        side: "yes" or "no"
        action: "buy" or "sell"
        count: Number of contracts
        price: Price in cents (1-99)

    Returns:
        Order response dict or None if failed
    """
    path = "/trade-api/v2/portfolio/orders"
    url = f"{API_BASE}/portfolio/orders"

    body = {
        "ticker": ticker,
        "side": side,
        "action": action,
        "count": count,
        "type": "limit",
    }

    # Set price based on side
    if side == "yes":
        body["yes_price"] = price
    else:
        body["no_price"] = price

    headers = get_auth_headers("POST", path)
    response = requests.post(url, json=body, headers=headers)

    if response.status_code in (200, 201):
        return response.json()
    else:
        print(f"Order failed: {response.status_code} - {response.text}")
        return None


def buy(ticker, side, count, price):
    """Buy contracts at limit price."""
    return place_limit_order(ticker, side, "buy", count, price)


def sell(ticker, side, count, price):
    """Sell contracts at limit price."""
    return place_limit_order(ticker, side, "sell", count, price)


def get_temperature_positions(debug=False):
    """
    Get positions for all temperature markets (KXHIGH* and KXLOW* tickers).

    Returns:
        List of position dicts with ticker, side, position (count)
    """
    path = "/trade-api/v2/portfolio/positions"
    url = f"{API_BASE}/portfolio/positions"

    headers = get_auth_headers("GET", path)
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        data = response.json()
        all_positions = data.get("market_positions", [])
        # Filter to temperature market tickers (KXHIGH* and KXLOW*)
        temp_positions = [p for p in all_positions
                         if p.get("ticker", "").startswith(("KXHIGH", "KXLOW"))]
        if debug:
            print(f"Raw API response: {len(all_positions)} total positions")
            for p in temp_positions:
                print(f"  {p}")
        return temp_positions
    else:
        print(f"Failed to get positions: {response.status_code} - {response.text}")
        return []


def get_market_price(ticker):
    """
    Get current market prices for a ticker.

    Args:
        ticker: Market ticker

    Returns:
        Dict with yes_ask, yes_bid, no_ask, no_bid or None
    """
    url = f"{API_BASE}/markets/{ticker}"
    response = requests.get(url)

    if response.status_code == 200:
        data = response.json()
        market = data.get("market", {})
        return {
            "yes_ask": market.get("yes_ask"),
            "yes_bid": market.get("yes_bid"),
            "no_ask": market.get("no_ask"),
            "no_bid": market.get("no_bid"),
        }
    else:
        print(f"Failed to get market: {response.status_code}")
        return None


def get_market_info(ticker):
    """
    Get full market info including close time.

    Returns:
        Dict with market details including close_time, or None
    """
    url = f"{API_BASE}/markets/{ticker}"
    response = requests.get(url)

    if response.status_code == 200:
        data = response.json()
        return data.get("market", {})
    else:
        print(f"Failed to get market info: {response.status_code}")
        return None


def cancel_order(order_id):
    """
    Cancel an open order on Kalshi.

    Args:
        order_id: The order ID to cancel

    Returns:
        True if cancelled successfully, False otherwise
    """
    path = f"/trade-api/v2/portfolio/orders/{order_id}"
    url = f"{API_BASE}/portfolio/orders/{order_id}"

    headers = get_auth_headers("DELETE", path)
    response = requests.delete(url, headers=headers)

    if response.status_code in (200, 204):
        print(f"Order {order_id} cancelled successfully")
        return True
    else:
        print(f"Failed to cancel order {order_id}: {response.status_code} - {response.text}")
        return False


def get_open_orders():
    """
    Get all open orders.

    Returns:
        List of open order dicts
    """
    path = "/trade-api/v2/portfolio/orders"
    url = f"{API_BASE}/portfolio/orders"

    headers = get_auth_headers("GET", path)
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        data = response.json()
        return data.get("orders", [])
    else:
        print(f"Failed to get orders: {response.status_code} - {response.text}")
        return []
