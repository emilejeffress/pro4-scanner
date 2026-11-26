"""PRO4 cloud scanner — one run. GitHub Actions calls it on a schedule.

Each run, for every enabled pair:
  * if a new hourly close needs checking or a paper trade is open, pulls the last 5,000 five-minute bars
    from Twelve Data (1 API credit);
  * follows the open paper trade bar by bar to TP / SL / time exit;
  * checks the newest hourly close with that pair's strategy and opens a paper trade on a signal;
  * sends phone pushes through ntfy and writes docs/data/{journal,state,status}.json for the dashboard.
"""
import json, os, sys, datetime, urllib.request, urllib.parse
from zoneinfo import ZoneInfo
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import strategy as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "docs", "data")
NY = ZoneInfo("America/New_York")


def load(name, default):
    try:
        with open(os.path.join(DATA, name)) as f: return json.load(f)
    except Exception: return default


def save(name, obj):
    with open(os.path.join(DATA, name), "w") as f: json.dump(obj, f, indent=1)


def now_ny_minutes():
    d = datetime.datetime.now(NY)
    return int(datetime.datetime(d.year, d.month, d.day, d.hour, d.minute, tzinfo=datetime.timezone.utc).timestamp() // 60) + d.second / 60


def fmt(t):
    return (datetime.datetime(1970, 1, 1) + datetime.timedelta(minutes=int(t))).strftime("%Y-%m-%d %H:%M")


def parse_dt(s):
    d = datetime.datetime.strptime(s if len(s) > 10 else s + " 00:00:00", "%Y-%m-%d %H:%M:%S")
    return int((d - datetime.datetime(1970, 1, 1)).total_seconds() // 60)


def fetch_m5(pair, key, outputsize=5000):
    q = urllib.parse.urlencode(dict(symbol=pair, interval="5min", outputsize=outputsize, timezone="America/New_York", order="asc", apikey=key))
    with urllib.request.urlopen(f"https://api.twelvedata.com/time_series?{q}", timeout=30) as r:
        js = json.load(r)
    if js.get("status") == "error" or "values" not in js:
        raise RuntimeError(js.get("message", "no data"))
    return sorted(((parse_dt(v["datetime"]), float(v["open"]), float(v["high"]), float(v["low"]), float(v["close"])) for v in js["values"]),
                  key=lambda x: x[0])


def push(title, body, kind, cfg):
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic: return
    headers = {"Title": title.encode("ascii", "ignore").decode(), "Priority": "high" if kind in ("BUY", "SELL") else "default",
               "Tags": {"BUY": "green_circle", "SELL": "red_circle", "WARN": "warning"}.get(kind, "bell")}
    if cfg.get("dashboard_url"): headers["Click"] = cfg["dashboard_url"]
    try:
        urllib.request.urlopen(urllib.request.Request(f"https://ntfy.sh/{urllib.parse.quote(topic)}", data=body.encode(), headers=headers), timeout=15)
    except Exception as e:
        print("ntfy failed:", e)


def run(now=None, fetch=None, notify=None, quiet=False):
    with open(os.path.join(ROOT, "config.json")) as f: cfg = json.load(f)
    now = now_ny_minutes() if now is None else now
    fetch = fetch or (lambda p: fetch_m5(p, os.environ.get("TWELVE_DATA_KEY", "")))
    notify = notify or (lambda title, body, kind: push(title, body, kind, cfg))
    journal = load("journal.json", []); state = load("state.json", {}); status = load("status.json", {})
    for k in ("last_eval", "open", "credits", "test_state", "rates"): state.setdefault(k, {})
    st = cfg["strategy"]; day = fmt(now)[:10]
    credits = state["credits"].get(day, 0); log = []; pairs_status = {}
    last_h1_close = int(now // 60) * 60
    max_entry = cfg.get("max_entry_delay_minutes", cfg["max_signal_delay_minutes"])
    for pair, pc in cfg["pairs"].items():
        if not pc.get("on"): continue
        strat = pc.get("strategy", "connors"); test = bool(pc.get("test"))
        ses = S.parse_window(st["session_ny"] if strat == "connors" else "07:00-16:00")
        info = dict(strategy=S.STRATEGY_NAMES[strat], test=test, spread=pc["spread_pips"])
        open_tr = state["open"].get(pair)
        needs_eval = (state["last_eval"].get(pair, 0) < last_h1_close and S.in_window(last_h1_close % 1440, ses)
                      and S.market_open(last_h1_close - 5) and now - last_h1_close <= cfg["max_signal_delay_minutes"])
        if not open_tr and not needs_eval:
            prev = status.get("pairs", {}).get(pair, {})
            prev.update(info, note="Outside session" if not S.in_window(int(now) % 1440, ses) else "Waiting for the next hourly close")
            pairs_status[pair] = prev; continue
        try:
            rows = fetch(pair); credits += 1
        except Exception as e:
            pairs_status[pair] = dict(status.get("pairs", {}).get(pair, {}), **info, note=f"Data error: {e}"); log.append(f"{fmt(now)} {pair}: data error {e}"); continue
        rows = [r for r in rows if r[0] + 5 <= now + 0.05 and S.market_open(r[0])]
        if len(rows) < 2600:
            pairs_status[pair] = dict(info, note="Not enough history"); continue
        a = np.array(rows); bars = dict(t=a[:, 0].astype(np.int64), o=a[:, 1], h=a[:, 2], l=a[:, 3], c=a[:, 4])
        F = S.build(bars, pair, cfg); n = len(bars["t"]); last = n - 1
        price = float(bars["c"][last])
        base, quote = pair.split("/")                                  # live USD conversion rates for cross pip values
        if pair == "USD/JPY": state["rates"]["JPY"] = 1 / price
        if pair == "USD/CHF": state["rates"]["CHF"] = 1 / price
        if pair == "GBP/USD": state["rates"]["GBP"] = price
        if pair == "EUR/USD": state["rates"]["EUR"] = price
        rates = state["rates"]; d = S.digits(pair)
        # --- follow the open paper trade ---
        if open_tr:
            for k in range(n):
                if bars["t"][k] < open_tr["next_t"]: continue
                res = S.step_trade(open_tr, bars["h"][k], bars["l"][k], bars["c"][k])
                open_tr["next_t"] = int(bars["t"][k] + 5)
                if res:
                    why, px = res; sgn = 1 if open_tr["side"] == "BUY" else -1
                    pips = sgn * (px - open_tr["entry"]) / F["pip"] - open_tr["comm_pips"]
                    pv = S.pip_value_per_lot(pair, px, rates)
                    for jr in journal:
                        if jr["id"] == open_tr["id"]:
                            jr.update(status=why, exit=px, exit_time=int(bars["t"][k] + 5), pips=round(pips, 1), R=round(pips / open_tr["sl_pips"], 3),
                                      usd=round(pips * pv * jr["lots"], 2))
                    tag = "TEST " if open_tr.get("test") else ""
                    notify(f"{tag}{pair} {open_tr['side']} closed: {why} ({pips:+.1f} pips, ${pips * pv * open_tr['lots']:+.2f})",
                           f"Entry {open_tr['entry']:.{d}f} -> exit {px:.{d}f} at {fmt(bars['t'][k] + 5)} NY", "INFO" if why == "TP" else "WARN")
                    log.append(f"{fmt(now)} {pair}: closed {why} {pips:+.1f}p")
                    state["open"].pop(pair, None); open_tr = None
                    if test:   # live-test grading for TEST strategies
                        ts, s = S.test_status(journal, pair); prev = state["test_state"].get(pair, "testing")
                        if ts != prev:
                            state["test_state"][pair] = ts
                            if ts == "passed":
                                notify(f"{pair} PASSED its live test", f"{s['n']} paper trades, {s['win']:.0%} wins, {s['avgR']:+.2f}R per trade. Its grade is now a letter and alerts no longer say paper only. Start at 0.01-0.03 lot.", "INFO")
                            elif ts == "failing":
                                notify(f"{pair} is failing its live test", f"{s['n']} paper trades, {s['win']:.0%} wins, {s['avgR']:+.2f}R per trade. Consider switching it off (config.json: \"on\": false).", "WARN")
                    break
        # --- check the newest hourly close ---
        i_sig = None
        for k in range(last, max(-1, last - 15), -1):
            if F["nb"][k]: i_sig = k; break
        ev = S.evaluate(F, i_sig if i_sig is not None else last, cfg, strat)
        if needs_eval and i_sig is not None and int(bars["t"][i_sig] + 5) == last_h1_close:
            state["last_eval"][pair] = last_h1_close
            late = now - last_h1_close
            passed = test and state["test_state"].get(pair) == "passed"
            label = ("PASSED TEST " if passed else "TEST paper only ") if test else ""
            if ev and ev["signal"] and late > max_entry:
                log.append(f"{fmt(now)} {pair}: {ev['signal']} skipped (scan {late:.0f} min late)")
            elif ev and ev["signal"]:
                if open_tr:
                    log.append(f"{fmt(now)} {pair}: {ev['signal']} ignored (trade already open)")
                else:
                    tr = S.make_trade(F, i_sig, ev["signal"], price, cfg, now, strat, rates)
                    if tr is None:
                        log.append(f"{fmt(now)} {pair}: {ev['signal']} skipped (target too small)")
                    else:
                        pv = S.pip_value_per_lot(pair, tr["entry"], rates)
                        lots = cfg.get("fixed_lot") or max(0.01, int(cfg["balance_usd"] * cfg["risk_percent"] / 100 / (tr["sl_pips"] * pv) * 100) / 100)
                        tr.update(id=f"{tr['signal_time']}-{pair.replace('/', '')}", next_t=int(bars["t"][last] + 5), lots=lots, test=test)
                        state["open"][pair] = tr
                        journal.insert(0, dict(id=tr["id"], pair=pair, side=tr["side"], strategy=S.STRATEGY_NAMES[strat], test=test,
                                               signal_time=tr["signal_time"], opened_time=int(now), entry=tr["entry"], sl=tr["sl"], tp=tr["tp"],
                                               sl_pips=tr["sl_pips"], tp_pips=tr["tp_pips"], lots=lots, status="OPEN"))
                        win_usd = (tr["tp_pips"] - tr["comm_pips"]) * pv * lots; loss_usd = (tr["sl_pips"] + tr["comm_pips"]) * pv * lots
                        hold = f" Close by hand after {tr['hold_bars'] // 12}h if neither hits." if tr["hold_bars"] else ""
                        notify(f"{label}{tr['side']} {pair} @ {tr['entry']:.{d}f} | TP {tr['tp']:.{d}f} | SL {tr['sl']:.{d}f}",
                               (("TEST strategy, paper only. Don't use real money yet.\n" if test and not passed else "")
                                + f"{S.STRATEGY_NAMES[strat]} signal at the {fmt(tr['signal_time'])[11:]} NY hourly close.\n"
                                + f"TP {tr['tp']:.{d}f} (+{tr['tp_pips']}p)  SL {tr['sl']:.{d}f} (-{tr['sl_pips']}p)\n"
                                + f"Lot {lots:.2f}: win ~ +${win_usd:.2f}, loss ~ -${loss_usd:.2f}. Set both in MT4 and leave it.{hold}"), tr["side"])
                        log.append(f"{fmt(now)} {pair}: {tr['side']} opened @ {tr['entry']:.{d}f}")
        if open_tr: state["open"][pair] = open_tr
        pairs_status[pair] = dict(info, price=price, bar_time=int(bars["t"][last] + 5), note="", eval=ev)
    state["credits"] = {day: credits}
    test_info = {p: dict(zip(("state", "stats"), S.test_status(journal, p))) for p, pc in cfg["pairs"].items() if pc.get("on") and pc.get("test")}
    status = dict(last_run=int(now), last_run_text=fmt(now) + " NY", credits_today=credits, pairs=pairs_status, open=state["open"],
                  log=(log + status.get("log", []))[:60], tests=test_info, test_rules=S.TEST_RULES, strategy=cfg["strategy"],
                  settings=dict(lot=cfg.get("fixed_lot"), commission=cfg["commission_usd_per_lot_round_trip"]))
    save("journal.json", journal); save("state.json", state); save("status.json", status)
    if not quiet: print(fmt(now), "credits", credits, log)
    return log


if __name__ == "__main__":
    run()
