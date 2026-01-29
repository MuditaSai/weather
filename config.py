# Kalshi API credentials
KALSHI_API_KEY = "99a60942-ff57-4583-8eee-8d25f6e30ea4"
KALSHI_PRIVATE_KEY_PATH = "/Users/mudita/Desktop/weather-key.txt"

# API base URL
API_BASE = "https://api.elections.kalshi.com/trade-api/v2"

# ============================================================
# TEMPERATURE MARKETS
# ============================================================
# All available temperature markets with NWS station codes

MARKETS = {
    # HIGH temperature markets
    # Gridpoints from: https://api.weather.gov/points/{lat},{lon}
    "KXHIGHAUS": {"city": "Austin", "type": "high", "nws_office": "EWX", "nws_gridpoint": "156,91"},
    "KXHIGHNY": {"city": "New York", "type": "high", "nws_office": "OKX", "nws_gridpoint": "33,35"},
    "KXHIGHMIA": {"city": "Miami", "type": "high", "nws_office": "MFL", "nws_gridpoint": "110,50"},
    "KXHIGHTNOLA": {"city": "New Orleans", "type": "high", "nws_office": "LIX", "nws_gridpoint": "68,88"},
    "KXHIGHDEN": {"city": "Denver", "type": "high", "nws_office": "BOU", "nws_gridpoint": "63,62"},
    "KXHIGHLAX": {"city": "Los Angeles", "type": "high", "nws_office": "LOX", "nws_gridpoint": "155,45"},
    "KXHIGHCHI": {"city": "Chicago", "type": "high", "nws_office": "LOT", "nws_gridpoint": "76,73"},
    "KXHIGHPHIL": {"city": "Philadelphia", "type": "high", "nws_office": "PHI", "nws_gridpoint": "50,76"},
    "KXHIGHTSEA": {"city": "Seattle", "type": "high", "nws_office": "SEW", "nws_gridpoint": "125,68"},
    "KXHIGHTSFO": {"city": "San Francisco", "type": "high", "nws_office": "MTR", "nws_gridpoint": "85,105"},
    "KXHIGHTDC": {"city": "Washington DC", "type": "high", "nws_office": "LWX", "nws_gridpoint": "97,71"},
    "KXHIGHTLV": {"city": "Las Vegas", "type": "high", "nws_office": "VEF", "nws_gridpoint": "123,98"},

    # LOW temperature markets
    "KXLOWTNYC": {"city": "New York", "type": "low", "nws_office": "OKX", "nws_gridpoint": "33,35"},
    "KXLOWTLAX": {"city": "Los Angeles", "type": "low", "nws_office": "LOX", "nws_gridpoint": "155,45"},
    "KXLOWTDEN": {"city": "Denver", "type": "low", "nws_office": "BOU", "nws_gridpoint": "63,62"},
    "KXLOWTCHI": {"city": "Chicago", "type": "low", "nws_office": "LOT", "nws_gridpoint": "76,73"},
    "KXLOWTMIA": {"city": "Miami", "type": "low", "nws_office": "MFL", "nws_gridpoint": "110,50"},
    "KXLOWTPHIL": {"city": "Philadelphia", "type": "low", "nws_office": "PHI", "nws_gridpoint": "50,76"},
    "KXLOWTAUS": {"city": "Austin", "type": "low", "nws_office": "EWX", "nws_gridpoint": "156,91"},
}

# ============================================================
# STRATEGY PARAMETERS - HEDGED YES STRATEGY
# ============================================================
# Buy YES on 2 adjacent buckets that BRACKET the forecast
# Prefer one bucket below and one at/above forecast for best coverage
# Win 100¢ if temp lands in either bucket = guaranteed profit

# Hedged YES Strategy
MAX_BUCKET_PRICE = 50       # Only buy if bucket price ≤50¢
MAX_TOTAL_COST = 100        # Max combined cost for 2 buckets (≤$1.00)
MIN_BUCKET_PRICE = 2        # Skip buckets with <2¢ price (<2% probability) - likely worthless

# Timing
FORECAST_CHECK_INTERVAL = 300   # Check forecast every 5 min (300 sec)
