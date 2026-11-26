"""Strategy logic for the PRO4 cloud scanner. Pure functions, no network.

Strategies
  connors      Connors RSI-2 (H1): the proven strategy (EUR/USD, GBP/USD, USD/CHF, USD/JPY, EUR/JPY)
  nzd_dip      NZD/USD TEST strategy (paper only until it passes its live test)
  eurchf_stoch EUR/CHF TEST strategy (paper only until it passes its live test)

Time convention: timestamps are "New York minutes since 1970-01-01" (naive NY wall-clock minutes), as requested
from Twelve Data with timezone=America/New_York. A 5-minute bar starting at t closes at t + 5.
"""
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

USD_PER = {"USD": 1.0, "JPY": 1 / 157.0, "GBP": 1.32, "CHF": 1 / 0.84, "CAD": 1 / 1.42, "EUR": 1.10, "AUD": 0.66, "NZD": 0.55}
STRATEGY_NAMES = {"connors": "Connors RSI-2 (H1)", "nzd_dip": "NZD dip-buy (TEST)", "eurchf_stoch": "EUR/CHF stoch reversal (TEST)"}


def pip(pair): return 1e-2 if "JPY" in pair else 1e-4
def digits(pair): return 3 if "JPY" in pair else 5


def pip_value_per_lot(pair, price, rates=None):
    """USD value of one pip on one standard lot."""
    base, quote = pair.split("/"); units = pip(pair) * 100000
    if quote == "USD": return 10.0
    if base == "USD": return units / price
    r = (rates or {}).get(quote) or USD_PER[quote]
    return units * r


def market_open(t):
    tod = t % 1440; dow = ((t // 1440) + 4) % 7                 # 0 = Sunday
    if dow == 6: return False
    if dow == 5 and tod >= 17 * 60: return False
    if dow == 0 and tod < 17 * 60: return False
    return True


def parse_window(s):
    a, b = s.replace(" ", "").split("-")
    f = lambda x: int(x.split(":")[0]) * 60 + int(x.split(":")[1])
    return f(a), f(b)


def in_window(m, w):
    s, e = w
    if s == e: return False
    return (s <= m < e) if s < e else (m >= s or m < e)


# ---------------- indicators (MetaTrader formulas) ----------------
def sma(x, p):
    """Window average; like numpy convolve, only windows that contain a NaN are NaN."""
    x = np.asarray(x, float); out = np.full(len(x), np.nan)
    if len(x) >= p: out[p - 1:] = sliding_window_view(x, p).mean(axis=1)
    return out


def rolling_std(x, p):
    m = sma(x, p); m2 = sma(np.asarray(x, float) ** 2, p)
    return np.sqrt(np.maximum(m2 - m * m, 0))


def rolling_min(x, p):
    out = np.full(len(x), np.nan); out[p - 1:] = sliding_window_view(np.asarray(x, float), p).min(axis=1); return out


def rolling_max(x, p):
    out = np.full(len(x), np.nan); out[p - 1:] = sliding_window_view(np.asarray(x, float), p).max(axis=1); return out


def pct_rank(x, p):
    out = np.full(len(x), np.nan)
    w = sliding_window_view(np.nan_to_num(np.asarray(x, float), nan=np.inf), p)
    out[p - 1:] = (w < w[:, -1:]).sum(axis=1) / (p - 1)
    return out


def rsi_wilder(c, p):
    n = len(c); r = np.full(n, np.nan)
    if n <= p: return r
    d = np.diff(c); g = np.where(d > 0, d, 0.0); l = np.where(d < 0, -d, 0.0)
    ag = g[:p].mean(); al = l[:p].mean()
    r[p] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(p + 1, n):
        ag = (ag * (p - 1) + g[i - 1]) / p; al = (al * (p - 1) + l[i - 1]) / p
        r[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return r


def true_range(h, l, c):
    pc = np.r_[c[0], c[:-1]]
    return np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))


def adx_wilder(h, l, c, p=14):
    n = len(c); out = np.full(n, np.nan)
    if n < 2 * p + 2: return out
    sTR = sP = sM = 0.0; adx = 0.0; dxs = 0.0; cnt = 0
    for i in range(1, n):
        tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        up = h[i] - h[i - 1]; dn = l[i - 1] - l[i]
        pdm = up if (up > dn and up > 0) else 0.0
        mdm = dn if (dn > up and dn > 0) else 0.0
        if i <= p:
            sTR += tr; sP += pdm; sM += mdm
            if i < p: continue
        else:
            sTR = sTR - sTR / p + tr; sP = sP - sP / p + pdm; sM = sM - sM / p + mdm
        pdi = 100 * sP / sTR if sTR > 0 else 0.0
        mdi = 100 * sM / sTR if sTR > 0 else 0.0
        dx = 100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) > 0 else 0.0
        if cnt < p:
            dxs += dx; cnt += 1
            if cnt == p: adx = dxs / p; out[i] = adx
        else:
            adx = (adx * (p - 1) + dx) / p; out[i] = adx
    return out


# ---------------- shared hourly features ----------------
def build(bars, pair, cfg):
    """bars: numpy arrays t,o,h,l,c (5-minute, closed, market hours only). Hourly bars + every indicator used."""
    st = cfg["strategy"]; t = bars["t"]; key = (t // 60) * 60
    starts = np.r_[0, np.nonzero(np.diff(key))[0] + 1]; ends = np.r_[starts[1:], len(t)]
    h1 = dict(t=key[starts], c=bars["c"][ends - 1], h=np.maximum.reduceat(bars["h"], starts), l=np.minimum.reduceat(bars["l"], starts))
    J = np.searchsorted(h1["t"] + 60, t + 5, side="right") - 1          # last hourly bar closed at each 5-min close
    nb = np.r_[True, J[1:] != J[:-1]]                                     # this 5-min close is an hourly close
    c1 = h1["c"]
    lo14, hi14 = rolling_min(h1["l"], 14), rolling_max(h1["h"], 14)
    raw = (c1 - lo14) / np.where(hi14 - lo14 > 0, hi14 - lo14, np.nan)
    Ks = sma(np.nan_to_num(100 * raw, nan=50), 3); D = sma(Ks, 3)
    mid, sd = sma(c1, 20), rolling_std(c1, 20)
    return dict(pair=pair, pip=pip(pair), bars=bars, h1=h1, J=J, nb=nb,
                sma_trend=sma(c1, st["trend_sma_hours"]), sma200=sma(c1, 200), sma_target=sma(c1, st["target_sma_hours"]),
                rsi2=rsi_wilder(c1, 2), adx=adx_wilder(h1["h"], h1["l"], c1, 14), atr=sma(true_range(h1["h"], h1["l"], c1), 14),
                stoch_raw=raw, Ks=Ks, D=D, bbw=pct_rank(4 * sd / mid, 100))


def evaluate(F, i, cfg, strat="connors"):
    """Conditions at the close of 5-minute bar i (using the last closed hourly bar)."""
    st = cfg["strategy"]; j = int(F["J"][i])
    if j < 1: return None
    T = int(F["bars"]["t"][i] + 5); m = T % 1440; dow = ((T // 1440) + 4) % 7
    fresh = bool(F["nb"][i]); P = lambda k: float(F[k][j])
    if strat == "connors":
        vals = dict(close_h1=float(F["h1"]["c"][j]), sma_trend=P("sma_trend"), rsi2=P("rsi2"), adx=P("adx"), atr_pips=P("atr") / F["pip"])
        if any(np.isnan(v) for v in vals.values()): return None
        ses = in_window(m, parse_window(st["session_ny"])) and not in_window(m, parse_window(st["rollover_block_ny"]))
        L = st["rsi2_threshold"]
        buy = [ses and fresh, vals["close_h1"] > vals["sma_trend"], vals["rsi2"] < L, vals["adx"] > st["min_adx"]]
        sell = [ses and fresh, vals["close_h1"] < vals["sma_trend"], vals["rsi2"] > 100 - L, vals["adx"] > st["min_adx"]]
        labels = ["Hourly close in session", "Trend (200-hr avg)", f"RSI(2) < {L}", f"ADX > {st['min_adx']}"]
        slabels = ["Hourly close in session", "Trend (200-hr avg)", f"RSI(2) > {100 - L}", f"ADX > {st['min_adx']}"]
        show = dict(rsi2=vals["rsi2"], adx=vals["adx"], trend="up" if vals["close_h1"] > vals["sma_trend"] else "down")
    else:
        ses = (420 <= m < 960) and not (dow == 5 and m >= 900)
        bar_c, bar_o = float(F["bars"]["c"][i]), float(F["bars"]["o"][i])
        if strat == "nzd_dip":
            c, s, r, sr = float(F["h1"]["c"][j]), P("sma200"), P("rsi2"), P("stoch_raw")
            buy = [ses and fresh, c > s, r < 15, sr < 0.15, bar_c > bar_o]
            sell = [ses and fresh, c < s, r > 85, sr > 0.85, bar_c < bar_o]
            labels = ["Hourly close in session", "Above 200-hr avg", "RSI(2) < 15", "Stochastic bottom 15%", "Bullish 5-min candle"]
            slabels = ["Hourly close in session", "Below 200-hr avg", "RSI(2) > 85", "Stochastic top 15%", "Bearish 5-min candle"]
            show = dict(rsi2=r, stoch=sr * 100 if sr == sr else float("nan"), trend="up" if c > s else "down")
        else:   # eurchf_stoch
            k, d, kp, dp, ax, w = P("Ks"), P("D"), float(F["Ks"][j - 1]), float(F["D"][j - 1]), P("adx"), P("bbw")
            buy = [ses and fresh, k > d and kp <= dp and k < 20, ax > 25, w > 0.7]
            sell = [ses and fresh, k < d and kp >= dp and k > 80, ax > 25, w > 0.7]
            labels = ["Hourly close in session", "Stoch crosses up below 20", "ADX > 25", "Bollinger width top 30%"]
            slabels = ["Hourly close in session", "Stoch crosses down above 80", "ADX > 25", "Bollinger width top 30%"]
            show = dict(stoch_k=k, adx=ax, bb_width_rank=w * 100 if w == w else float("nan"))
        vals = dict(atr_pips=P("atr") / F["pip"])
    buy = [bool(x) for x in buy]; sell = [bool(x) for x in sell]
    out = dict(close_time=T, h1_start=int(F["h1"]["t"][j]), buy=buy, sell=sell, buy_labels=labels, sell_labels=slabels,
               signal="BUY" if all(buy) else "SELL" if all(sell) else None, atr_pips=vals["atr_pips"])
    out.update({k: (None if (isinstance(v, float) and v != v) else (round(v, 1) if isinstance(v, float) else v)) for k, v in show.items()})
    return out


def make_trade(F, i, side, entry_bid, cfg, now_t, strat="connors", rates=None):
    st = cfg["strategy"]; pair = F["pair"]; p = F["pip"]; j = int(F["J"][i])
    spr = cfg["pairs"][pair]["spread_pips"] * p
    entry = entry_bid + spr if side == "BUY" else entry_bid
    pv = pip_value_per_lot(pair, entry_bid, rates)
    comm_p = cfg["commission_usd_per_lot_round_trip"] / pv
    hold = None
    if strat == "connors":
        sl_p = round(max(st["stop_atr_multiple"] * F["atr"][j] / p, 4.0), 1)
        dist = (F["sma_target"][j] - entry) / p if side == "BUY" else (entry - F["sma_target"][j]) / p
        tp_p = round(st["tp_fraction_to_target_sma"] * dist, 1)
        if not (tp_p >= st["min_tp_pips"]): return None
    elif strat == "nzd_dip":
        tp_p = round(3.0 / (pv * 0.03) + comm_p, 1); sl_p = round(tp_p * 4.0, 1); hold = 288
    else:
        a = F["atr"][j] / p
        if not (a > 0): return None
        tp_p = round(max(0.5 * a, 3.0), 1); sl_p = round(max(2.0 * a, 4.0), 1); hold = 864
    sgn = 1 if side == "BUY" else -1
    return dict(pair=pair, side=side, strategy=strat, signal_time=int(F["bars"]["t"][i] + 5), opened_time=int(now_t),
                entry=float(entry), sl=float(entry - sgn * sl_p * p), tp=float(entry + sgn * tp_p * p),
                sl_pips=float(sl_p), tp_pips=float(tp_p), spread=float(spr), comm_pips=float(comm_p),
                hold_bars=hold, stepped=0, status="OPEN")


def step_trade(tr, h, l, c):
    """One 5-minute bar (bid). Time exit on the final bar first (if any), then SL, then TP."""
    sp = tr["spread"]
    if tr.get("hold_bars"):
        tr["stepped"] = tr.get("stepped", 0) + 1
        if tr["stepped"] >= tr["hold_bars"]:
            return "TIME", (c if tr["side"] == "BUY" else c + sp)
    if tr["side"] == "BUY":
        if l <= tr["sl"]: return "SL", tr["sl"]
        if h >= tr["tp"]: return "TP", tr["tp"]
    else:
        if h + sp >= tr["sl"]: return "SL", tr["sl"]
        if l + sp <= tr["tp"]: return "TP", tr["tp"]
    return None


# ---------------- live test for TEST strategies ----------------
TEST_RULES = dict(min_trades=15, min_win=0.80, fail_after=10, fail_win=0.70)


def test_status(journal, pair):
    L = [j for j in journal if j["pair"] == pair and j.get("test") and j["status"] in ("TP", "SL", "TIME")]
    n = len(L); wins = sum(1 for j in L if j["pips"] > 0); avgR = sum(j["R"] for j in L) / n if n else 0.0
    win = wins / n if n else 0.0
    if n >= TEST_RULES["min_trades"] and win >= TEST_RULES["min_win"] and avgR > 0: st = "passed"
    elif n >= TEST_RULES["fail_after"] and (win < TEST_RULES["fail_win"] or avgR < 0): st = "failing"
    else: st = "testing"
    return st, dict(n=n, win=win, avgR=avgR)
