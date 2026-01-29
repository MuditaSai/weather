"""
NWS Forecast Parser - Fetches hourly temperature forecasts from weather.gov

Supports multiple cities via NWS gridpoint API.
"""

import requests
from datetime import datetime, timedelta
from config import MARKETS


def get_nws_forecast(nws_office, nws_gridpoint):
    """
    Fetch hourly forecast from NWS gridpoint API.

    Args:
        nws_office: NWS office code (e.g., "LWX" for DC)
        nws_gridpoint: Grid coordinates (e.g., "97,71")

    Returns:
        List of (datetime, temp) tuples, or None if fetch fails
    """
    url = f"https://api.weather.gov/gridpoints/{nws_office}/{nws_gridpoint}/forecast/hourly"
    headers = {"User-Agent": "weather-trading-bot/1.0"}

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        forecast = []
        for period in data.get("properties", {}).get("periods", []):
            # Parse ISO timestamp
            start_time = period.get("startTime", "")
            temp = period.get("temperature")

            if start_time and temp is not None:
                # Parse: "2026-01-25T13:00:00-05:00"
                dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                # Convert to local naive datetime for simplicity
                dt = dt.replace(tzinfo=None)
                forecast.append((dt, temp))

        return forecast

    except Exception as e:
        print(f"Error fetching NWS forecast: {e}")
        return None


def get_forecast_for_market(series_ticker, target_date):
    """
    Get forecast for a specific market and date.

    Args:
        series_ticker: Kalshi series ticker (e.g., "KXHIGHTDC")
        target_date: date object for the target day

    Returns:
        dict with forecast_high or forecast_low depending on market type
    """
    if series_ticker not in MARKETS:
        print(f"Unknown market: {series_ticker}")
        return None

    market = MARKETS[series_ticker]
    city = market["city"]
    temp_type = market["type"]  # "high" or "low"
    nws_office = market["nws_office"]
    nws_gridpoint = market["nws_gridpoint"]

    forecast = get_nws_forecast(nws_office, nws_gridpoint)
    if not forecast:
        return None

    # Filter to target date
    day_temps = [(dt, temp) for dt, temp in forecast
                 if dt.date() == target_date]

    if not day_temps:
        print(f"No forecast data for {city} on {target_date}")
        return None

    temps_only = [t for _, t in day_temps]

    if temp_type == "high":
        # Find the high
        target_dt, target_temp = max(day_temps, key=lambda x: x[1])
        return {
            "city": city,
            "type": "high",
            "target_date": target_date.strftime("%Y-%m-%d"),
            "forecast_high": target_temp,
            "forecast_temp": target_temp,  # Generic field
            "time": target_dt.strftime("%I:%M %p"),
            "hourly_temps": temps_only,
            "min_temp": min(temps_only),
            "max_temp": max(temps_only),
        }
    else:
        # Find the low
        target_dt, target_temp = min(day_temps, key=lambda x: x[1])
        return {
            "city": city,
            "type": "low",
            "target_date": target_date.strftime("%Y-%m-%d"),
            "forecast_low": target_temp,
            "forecast_temp": target_temp,  # Generic field
            "time": target_dt.strftime("%I:%M %p"),
            "hourly_temps": temps_only,
            "min_temp": min(temps_only),
            "max_temp": max(temps_only),
        }


def get_tomorrow_forecast(series_ticker):
    """Get forecast for tomorrow for a specific market."""
    tomorrow = (datetime.now() + timedelta(days=1)).date()
    return get_forecast_for_market(series_ticker, tomorrow)


def get_today_forecast(series_ticker):
    """Get forecast for today for a specific market."""
    today = datetime.now().date()
    return get_forecast_for_market(series_ticker, today)


# Legacy functions for backwards compatibility with DC
def get_tomorrow_high():
    """Get DC high temp forecast for tomorrow (legacy)."""
    return get_tomorrow_forecast("KXHIGHTDC")


def get_today_high():
    """Get DC high temp forecast for today (legacy)."""
    return get_today_forecast("KXHIGHTDC")


if __name__ == "__main__":
    print("Testing NWS forecasts for all markets...\n")

    tomorrow = (datetime.now() + timedelta(days=1)).date()

    for ticker, market in MARKETS.items():
        forecast = get_forecast_for_market(ticker, tomorrow)
        if forecast:
            temp_type = "High" if market["type"] == "high" else "Low"
            print(f"{market['city']} ({ticker}):")
            print(f"  {temp_type}: {forecast['forecast_temp']}°F at {forecast['time']}")
        else:
            print(f"{market['city']} ({ticker}): No forecast available")
        print()
