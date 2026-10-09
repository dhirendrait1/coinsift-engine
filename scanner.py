import os
import sys
import time
import requests
import numpy as np
import pandas as pd

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

if not DISCORD_WEBHOOK_URL:
    print("Error: DISCORD_WEBHOOK_URL environment variable is missing.")
    sys.exit(1)

BINANCE_FUTURES_URL = "https://fapi.binance.com"
FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"

def get_fear_and_greed():
    try:
        res = requests.get(FEAR_GREED_URL, timeout=5).json()
        val = int(res["data"][0]["value"])
        classification = res["data"][0]["value_classification"]
        return val, classification
    except Exception:
        return 50, "Neutral"

def fetch_liquid_tickers(min_volume_usdt=20_000_000):
    url = f"{BINANCE_FUTURES_URL}/fapi/v1/ticker/24hr"
    res = requests.get(url, timeout=10).json()
    liquid_pairs = []
    
    for item in res:
        symbol = item["symbol"]
        quote_vol = float(item["quoteVolume"])
        if symbol.endswith("USDT") and quote_vol >= min_volume_usdt:
            liquid_pairs.append({
                "symbol": symbol,
                "volume": quote_vol,
                "price": float(item["lastPrice"]),
                "priceChangePercent": float(item["priceChangePercent"])
            })
            
    return sorted(liquid_pairs, key=lambda x: x["volume"], reverse=True)[:25]

def fetch_klines_and_funding(symbol):
    kline_url = f"{BINANCE_FUTURES_URL}/fapi/v1/klines?symbol={symbol}&interval=1h&limit=60"
    k_res = requests.get(kline_url, timeout=10).json()
    
    df = pd.DataFrame(k_res, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "q_vol", "trades", "taker_base", "taker_quote", "ignore"
    ])
    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["volume"] = df["volume"].astype(float)
    
    funding_url = f"{BINANCE_FUTURES_URL}/fapi/v1/premiumIndex?symbol={symbol}"
    f_res = requests.get(funding_url, timeout=10).json()
    funding_rate = float(f_res.get("lastFundingRate", 0.0))
    
    return df, funding_rate

def compute_indicators(df):
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]
    
    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
    
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=14).mean().iloc[-1]
    
    avg_vol = volume.rolling(window=24).mean().iloc[-1]
    rvol = volume.iloc[-1] / avg_vol if avg_vol > 0 else 1.0
    
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    rsi = (100 - (100 / (1 + rs))).iloc[-1]
    
    support = low.iloc[-10:].min()
    
    return {
        "price": close.iloc[-1],
        "ema20": ema20,
        "ema50": ema50,
        "atr": atr,
        "rvol": rvol,
        "rsi": rsi,
        "support": support
    }

def evaluate_setup(symbol, ind, funding_rate, fg_value):
    score = 0
    price = ind["price"]
    
    if price > ind["ema50"] and ind["ema20"] > ind["ema50"]:
        score += 15
    elif price > ind["ema50"]:
        score += 8
        
    if ind["rvol"] >= 2.0:
        score += 15
    elif ind["rvol"] >= 1.5:
        score += 10
        
    if 45 <= ind["rsi"] <= 65:
        score += 10
        
    if funding_rate < -0.0001:
        score += 20
    elif 0.0 <= funding_rate <= 0.00005:
        score += 15
        
    if ind["price"] >= ind["support"]:
        score += 15
        
    if 45 <= fg_value <= 65:
        score += 15
    elif fg_value < 30:
        score += 10
        
    atr = ind["atr"]
    support = ind["support"]
    entry_low = support
    entry_high = support + (0.5 * atr)
    stop_loss = support - (0.8 * atr)
    
    entry_mid = (entry_low + entry_high) / 2
    risk = entry_mid - stop_loss
    
    if risk <= 0:
        return None
        
    tp1 = entry_mid + (1.8 * risk)
