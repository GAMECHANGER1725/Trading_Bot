#!/usr/bin/env python3
"""Render the paper-trading books as a self-contained HTML dashboard.

Reads paper_state.json + paper_trades.csv (and optionally live prices) and
writes dashboard.html. Stdlib only, no external assets — the same file works
opened locally or published as an artifact.

Run: python3 dashboard.py
"""
import csv
import html
import json
import math
import os
from datetime import datetime, timezone

import paper_trader as pt

OUT_FILE = "dashboard.html"
SIGNIFICANCE_TARGET = 100  # trades per strategy before the comparison means much

CONTROLS = pt.CONTROL_BOOKS
SHOWN = pt.REAL_BOOKS + pt.CONTROL_BOOKS[:1]   # one control column is enough
SHORT = {**{k: f"Control {i + 1}" for i, k in enumerate(pt.CONTROL_BOOKS)},
         "v1_rsi_macd": "V1 RSI+MACD", "v2_stoch_mfi": "V2 Stoch+MFI"}
REAL = pt.REAL_BOOKS
SERIES = {**{k: "s0" for k in CONTROLS}, "v1_rsi_macd": "s1", "v2_stoch_mfi": "s2"}
LABEL = {**{k: f"Control {i + 1} · random entry" for i, k in enumerate(CONTROLS)},
         "v1_rsi_macd": "V1 · RSI + MACD",
         "v2_stoch_mfi": "V2 · Stochastic + MFI"}


# ------------------------------ data ------------------------------

def read_state(path=pt.STATE_FILE):
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f).get("portfolios", {})
    except (json.JSONDecodeError, OSError):
        return {}


def read_trades(path=pt.LOG_FILE):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if r.get("event") != "EXIT":
                continue
            note = r.get("note", "")
            pnl = None
            if "pnl=" in note:
                try:
                    pnl = float(note.split("pnl=")[1].split()[0])
                except (ValueError, IndexError):
                    pnl = None
            if pnl is None:
                continue
            rows.append({
                "time": r["bar_time"], "strategy": r["strategy"], "symbol": r["symbol"],
                "side": r["side"], "price": float(r["price"]), "pnl": pnl,
                "reason": note.split(" pnl=")[0],
            })
    return rows


def read_orders(path=pt.LOG_FILE, limit=250):
    """Every ENTRY and EXIT, newest first. read_trades() keeps only closed
    trades for the statistics; the order book wants both sides. 250 rather than a
    handful now that Orders is its own page — it is a ledger, not a preview."""
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if r.get("event") not in ("ENTRY", "EXIT"):
                continue
            note = r.get("note", "")
            pnl = None
            if "pnl=" in note:
                try:
                    pnl = float(note.split("pnl=")[1].split()[0])
                except (ValueError, IndexError):
                    pnl = None
            rows.append({"time": r["bar_time"], "strategy": r["strategy"],
                         "symbol": r["symbol"], "event": r["event"],
                         "side": r["side"], "price": float(r["price"]),
                         "pnl": pnl,
                         "reason": note.split(" pnl=")[0] if pnl is not None else ""})
    rows.reverse()
    return rows[:limit]


def portfolio_totals(stats, orders_all):
    """The two strategy books read as one account. The control books are
    instruments, not the user's money, so they are excluded here."""
    real = [stats[k] for k in REAL if k in stats]
    return {
        "net_worth": sum(r["equity"] for r in real),
        "cash": sum(r["cash"] for r in real),
        "unrealised": sum(r["unrealised"] for r in real),
        "base": pt.INITIAL_CAPITAL * len(real),
        "closed": sum(r["trades"] for r in real),
        "open": sum(len(r["positions"]) for r in real),
        "orders": sum(1 for o in orders_all if o["strategy"] in REAL),
        "wins": sum(r["wins"] for r in real),
    }


def live_prices():
    """Last close per symbol, plus each strategy's current read. Best-effort."""
    out = {}
    for sym in pt.SYMBOLS:
        try:
            raw = pt.fetch_klines(sym)
            bars = raw[:-1]
            ind = pt.compute_indicators(bars)
            i = len(bars) - 1
            reads = {}
            for name, cfg in pt.STRATEGIES.items():
                long_ok, short_ok, bull, bear, trending = cfg["signal"](ind, i, cfg)
                reads[name] = ("long" if long_ok else "short" if short_ok else
                               "chop" if not trending else "wait")
            out[sym] = {
                "price": ind["close"][i], "now": raw[-1]["close"],
                "ema": ind["ema"][100][i],
                "rsi": ind["rsi"][i], "stoch": ind["stoch"][i], "mfi": ind["mfi"][i],
                "adx": ind["adx"][i], "atr": ind["atr"][i], "reads": reads,
                "hist": [round(c, 8) for c in ind["close"][-pt.CHART_BARS:]],
                "bar_ms": bars[i]["closeTime"],
            }
        except Exception:  # noqa: BLE001 - dashboard renders fine without live data
            pass
    return out


def strategy_stats(trades, state, live=None):
    """Aggregate per strategy: equity, returns, win/loss, profit factor, t-stat."""
    stats = {}
    for strat in pt.STRATEGIES:
        acct = state.get(strat, {})
        ts = [t for t in trades if t["strategy"] == strat]
        pnls = [t["pnl"] for t in ts]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_win, gross_loss = sum(wins), abs(sum(losses))
        cash = acct.get("cash_equity", pt.INITIAL_CAPITAL)
        # Mark to market. Cash alone shows a flat $10,000 for a book holding
        # seven open positions, which reads as "this book has done nothing".
        unreal = 0.0
        for sym_, q in acct.get("positions", {}).items():
            mark = ((live or {}).get(sym_, {}).get("now")
                    or (live or {}).get(sym_, {}).get("price") or q["last"])
            d = ((mark - q["entry"]) if q["side"] == "long"
                 else (q["entry"] - mark))
            unreal += d * q["qty"]
        equity = cash + unreal
        mean = sum(pnls) / len(pnls) if pnls else 0.0
        # Sample t-stat on mean trade P&L — the honest read on whether any
        # apparent edge is distinguishable from noise at this sample size.
        t_stat, se = None, 0.0
        if len(pnls) >= pt.MIN_T_TRADES:
            var = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
            se = math.sqrt(var / len(pnls)) if var > 0 else 0.0
            t_stat = mean / se if se > 0 else None
        stats[strat] = {
            "equity": equity, "cash": cash, "unrealised": unreal,
            "base": pt.INITIAL_CAPITAL,
            "return_pct": (equity / pt.INITIAL_CAPITAL - 1) * 100,
            "trades": len(ts), "wins": len(wins), "losses": len(losses),
            "win_rate": len(wins) / len(ts) * 100 if ts else 0.0,
            "profit_factor": (gross_win / gross_loss) if gross_loss else
                             (float("inf") if gross_win else 0.0),
            "avg_trade": mean, "t_stat": t_stat, "se": se,
            "positions": acct.get("positions", {}),
            "halted": acct.get("halted_until_ms", 0) > __import__("time").time() * 1000,
        }
    return stats


def equity_curves(trades, state):
    """Equity over time per strategy, rebuilt from the trade log."""
    curves = {}
    for strat in pt.STRATEGIES:
        running = pt.INITIAL_CAPITAL
        pts = [(None, running)]
        for t in sorted((x for x in trades if x["strategy"] == strat),
                        key=lambda x: x["time"]):
            running += t["pnl"]
            pts.append((t["time"], running))
        curves[strat] = pts
    return curves


# ------------------------------ svg ------------------------------

def render_spark(hist, entry, sl, tp, side, width=280, height=76):
    """Price line for one position with its entry, stop and target marked.

    Drawn server-side as plain SVG rather than embedded from TradingView: the
    artifact sandbox blocks every external host, so a widget would render an
    empty box. The candles are the same Binance data the book trades on.
    """
    pts = [p for p in (hist or []) if p and p > 0]
    # 96 points across 280px is finer than the pixels; every other one is
    # visually identical and halves the page weight across ~40 cards
    if len(pts) > 48:
        pts = pts[::2]
    if len(pts) < 3:
        return '<div class="spark-empty">no price history yet</div>'
    lo, hi = min(pts + [sl, tp]), max(pts + [sl, tp])
    if hi <= lo:
        return '<div class="spark-empty">flat</div>'
    pad = 6
    span = hi - lo

    def y(v):
        return pad + (height - 2 * pad) * (1 - (v - lo) / span)

    def x(i):
        return pad + (width - 2 * pad) * (i / (len(pts) - 1))

    line = " ".join(f"{x(i):.0f},{y(v):.1f}" for i, v in enumerate(pts))
    area = f"{pad},{height - pad} {line} {width - pad},{height - pad}"
    won = (pts[-1] >= entry) if side == "long" else (pts[-1] <= entry)
    col = "var(--pos)" if won else "var(--neg)"
    rules = "".join(
        f'<line x1="{pad}" y1="{y(v):.1f}" x2="{width - pad}" y2="{y(v):.1f}" '
        f'class="rule {c}"/>'
        for v, c in ((sl, "sl"), (tp, "tp"), (entry, "en")) if lo <= v <= hi)
    return (
        f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="none" '
        f'class="spark" role="img" aria-label="Recent price with entry, stop and target">'
        f'<polygon points="{area}" fill="{col}" fill-opacity="0.10"/>'
        f'{rules}'
        f'<polyline points="{line}" fill="none" stroke="{col}" stroke-width="1.6" '
        f'stroke-linejoin="round"/>'
        f'<circle cx="{x(len(pts) - 1):.1f}" cy="{y(pts[-1]):.1f}" r="2.6" fill="{col}"/>'
        f'</svg>')


def render_chart(curves, width=840, height=260):
    all_vals = [v for pts in curves.values() for _, v in pts]
    if not all_vals or all(len(p) < 2 for p in curves.values()):
        return ('<div class="empty-chart"><span>No closed trades yet</span>'
                '<small>The curve starts drawing after the first position closes.</small></div>')
    lo, hi = min(all_vals), max(all_vals)
    pad_v = max((hi - lo) * 0.15, 1.0)
    lo, hi = lo - pad_v, hi + pad_v
    pad = 8
    n = max(max(len(p) for p in curves.values()) - 1, 1)

    def x(i):
        return pad + (width - 2 * pad) * (i / n)

    def y(v):
        return height - pad - (height - 2 * pad) * ((v - lo) / (hi - lo))

    parts = [f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="none" '
             f'role="img" aria-label="Equity curve by strategy">']
    parts.append('<defs>')
    for key in dict.fromkeys(SERIES.values()):
        parts.append(
            f'<linearGradient id="g-{key}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="var(--{key})" stop-opacity="0.22"/>'
            f'<stop offset="1" stop-color="var(--{key})" stop-opacity="0.01"/>'
            f'</linearGradient>')
    parts.append('</defs>')

    # baseline (starting capital) and faint horizontal grid
    for frac in (0.25, 0.5, 0.75):
        gy = pad + (height - 2 * pad) * frac
        parts.append(f'<line x1="{pad}" y1="{gy:.1f}" x2="{width - pad}" y2="{gy:.1f}" '
                     f'class="grid"/>')
    base_total = pt.INITIAL_CAPITAL
    if lo < base_total < hi:
        by = y(base_total)
        parts.append(f'<line x1="{pad}" y1="{by:.1f}" x2="{width - pad}" y2="{by:.1f}" '
                     f'class="baseline"/>')

    for strat, pts in curves.items():
        if len(pts) < 2:
            continue
        key = SERIES[strat]
        coords = [f"{x(i):.1f},{y(v):.1f}" for i, (_, v) in enumerate(pts)]
        area = (f'M{x(0):.1f},{height - pad:.1f} L' + " L".join(coords) +
                f' L{x(len(pts) - 1):.1f},{height - pad:.1f} Z')
        parts.append(f'<path d="{area}" fill="url(#g-{key})"/>')
        parts.append(f'<polyline points="{" ".join(coords)}" fill="none" '
                     f'stroke="var(--{key})" stroke-width="2.2" '
                     f'stroke-linejoin="round" stroke-linecap="round"/>')
        ex, ey = x(len(pts) - 1), y(pts[-1][1])
        parts.append(f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="4.5" '
                     f'fill="var(--{key})" stroke="var(--surface)" stroke-width="2"/>')
    parts.append("</svg>")
    return "".join(parts)


# ------------------------------ html ------------------------------

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#EDF0F2; --surface:#FFFFFF; --surface-2:#F6F8F9;
  --ink:#10161C; --muted:#5C6B78; --line:#D4DBE0;
  --accent:#2E5EAA; --s0:#8A8F98; --s1:#B4531F; --s2:#0F7A6C;
  --pos:#1B7A3E; --neg:#B3261E; --warn:#8A6410;
  --pos-bg:#E6F2EA; --neg-bg:#FBE9E7; --warn-bg:#FBF2DF;
  --shadow:0 1px 2px rgba(16,22,28,.06),0 8px 24px rgba(16,22,28,.05);
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --bg:#0D1418; --surface:#131C22; --surface-2:#18242B;
    --ink:#E4EBEF; --muted:#8798A5; --line:#233039;
    --accent:#6E9BE8; --s0:#7E838C; --s1:#E08040; --s2:#35B8A5;
    --pos:#3DBE6E; --neg:#E4574C; --warn:#D6A93C;
    --pos-bg:#12271B; --neg-bg:#2A1614; --warn-bg:#251E0E;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.3);
  }
}
:root[data-theme="dark"]{
  --bg:#0D1418; --surface:#131C22; --surface-2:#18242B;
  --ink:#E4EBEF; --muted:#8798A5; --line:#233039;
  --accent:#6E9BE8; --s0:#7E838C; --s1:#E08040; --s2:#35B8A5;
  --pos:#3DBE6E; --neg:#E4574C; --warn:#D6A93C;
  --pos-bg:#12271B; --neg-bg:#2A1614; --warn-bg:#251E0E;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.3);
}
body{
  background:var(--bg); color:var(--ink);
  font-family:"IBM Plex Sans",-apple-system,BlinkMacSystemFont,system-ui,sans-serif;
  font-size:15px; line-height:1.55; -webkit-font-smoothing:antialiased;
  padding:32px 20px 64px;
}
.wrap{max-width:1080px;margin:0 auto;display:flex;flex-direction:column;gap:22px}
.num{font-family:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  font-variant-numeric:tabular-nums}
.lbl{font-size:10.5px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;
  color:var(--muted)}

/* header */
header{display:flex;justify-content:space-between;align-items:flex-end;gap:20px;
  flex-wrap:wrap;padding-bottom:18px;border-bottom:1px solid var(--line)}
h1{font-family:"IBM Plex Serif",Georgia,serif;font-size:30px;font-weight:600;
  letter-spacing:-.015em;text-wrap:balance}
.tagline{color:var(--muted);font-size:13.5px;margin-top:3px;max-width:62ch}
.stamp{text-align:right;display:flex;flex-direction:column;gap:6px;align-items:flex-end}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;font-weight:600;
  padding:4px 10px;border-radius:100px;border:1px solid transparent}
.pill.live{background:var(--pos-bg);color:var(--pos);border-color:currentColor}
.pill.stale{background:var(--warn-bg);color:var(--warn);border-color:currentColor}
.dot{width:6px;height:6px;border-radius:50%;background:currentColor}

/* verdict */
.verdict{background:var(--surface);border:1px solid var(--line);border-radius:12px;
  padding:20px 22px;box-shadow:var(--shadow);display:flex;flex-direction:column;gap:14px}
.verdict-head{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
.verdict-head h2{font-size:19px;font-weight:600;letter-spacing:-.01em}
.verdict p{color:var(--muted);font-size:13.5px;max-width:78ch}
.meter{display:flex;flex-direction:column;gap:7px}
.meter-track{height:7px;border-radius:100px;background:var(--surface-2);
  border:1px solid var(--line);overflow:hidden;display:flex}
.meter-fill{background:var(--accent);height:100%}
.meter-row{display:flex;justify-content:space-between;align-items:baseline;gap:12px}

/* glossary */
.glossary{display:flex;flex-direction:column}
.gl{display:grid;grid-template-columns:minmax(150px,220px) 1fr;gap:20px;
  padding:13px 0;border-top:1px solid var(--line)}
.gl:first-child{border-top:0;padding-top:0}
.gl dt{font-weight:600;font-size:13.5px}
.gl dd{color:var(--muted);font-size:13.5px;max-width:76ch}
.gl dd em{font-style:normal;font-family:"IBM Plex Mono",ui-monospace,monospace;
  font-size:12.5px;background:var(--surface-2);padding:1px 5px;border-radius:4px}
@media (max-width:640px){.gl{grid-template-columns:1fr;gap:4px}}

/* nav */
.nav{display:flex;gap:2px;background:var(--surface);border:1px solid var(--line);
  border-radius:10px;padding:4px;box-shadow:var(--shadow);overflow-x:auto}
.nav a{flex:1 1 auto;text-align:center;white-space:nowrap;padding:8px 16px;
  border-radius:7px;font-size:13.5px;font-weight:500;text-decoration:none;
  color:var(--muted);transition:background .12s,color .12s}
.nav a:hover{background:var(--surface-2);color:var(--ink)}
.nav a.on{background:var(--accent);color:#fff}
.nav a.on:hover{background:var(--accent);color:#fff}

/* portfolio summary */
.summary{background:var(--surface);border:1px solid var(--line);border-radius:12px;
  box-shadow:var(--shadow);padding:22px 24px;display:flex;gap:28px;
  align-items:flex-start;flex-wrap:wrap}
.summary .big{display:flex;flex-direction:column;gap:3px;min-width:240px;flex:1 1 240px}
.summary .big .v{font-size:38px;font-weight:600;letter-spacing:-.03em;line-height:1.1}
.summary .sub{font-size:12.5px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));
  gap:18px;flex:2 1 420px}
.tile{display:flex;flex-direction:column;gap:2px}
.tile .v{font-size:20px;font-weight:600;letter-spacing:-.02em}

/* open position cards */
.pos-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(268px,1fr));gap:14px}
.holding{border:1px solid var(--line);border-radius:10px;padding:13px 14px;
  background:var(--surface-2);display:flex;flex-direction:column;gap:9px}
.pos-top{display:flex;justify-content:space-between;align-items:center;gap:10px}
.pos-top .sym{font-weight:600;font-size:15px;margin-right:7px}
.pos-pnl{font-size:14px;font-weight:600;text-align:right}
.pos-book{font-size:11px;color:var(--muted);margin-top:-6px}
.spark{width:100%;height:76px;display:block}
.spark-empty{height:76px;display:flex;align-items:center;justify-content:center;
  font-size:12px;color:var(--muted);border:1px dashed var(--line);border-radius:6px}
.rule{stroke-width:1;stroke-dasharray:3 3}
.rule.sl{stroke:var(--neg)} .rule.tp{stroke:var(--pos)} .rule.en{stroke:var(--muted)}
.pos-bar{height:4px;border-radius:100px;background:var(--neg);opacity:.25;overflow:hidden}
.pos-bar-fill{height:100%;background:var(--pos);opacity:1}
.pos-legs{display:flex;justify-content:space-between;gap:6px;font-size:10.5px;
  color:var(--muted);font-family:"IBM Plex Mono",ui-monospace,monospace}
.pos-legs i.k{display:inline-block;width:7px;height:2px;margin-right:4px;
  vertical-align:middle;border-radius:2px}
.pos-legs i.sl{background:var(--neg)} .pos-legs i.tp{background:var(--pos)}
.pos-legs i.en{background:var(--muted)}
.pos-foot{display:flex;justify-content:space-between;align-items:center;
  font-size:11.5px;padding-top:2px;border-top:1px solid var(--line)}
.pos-foot a{color:var(--accent);text-decoration:none;font-weight:500}
.pos-foot a:hover{text-decoration:underline}
.ev{font-size:10px;font-weight:600;padding:2px 6px;border-radius:4px;letter-spacing:.04em}
.ev.entry{background:var(--surface-2);color:var(--accent);border:1px solid var(--line)}
.ev.exit{background:var(--surface-2);color:var(--muted);border:1px solid var(--line)}

#tv:empty{display:none}
#bigchart{width:100%;height:320px;display:block}
.chart-note{font-size:12px;color:var(--muted);margin-top:8px}
#symsel{font:inherit;font-size:13px;padding:5px 9px;border-radius:7px;
  border:1px solid var(--line);background:var(--surface-2);color:var(--ink)}

/* cards */
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:12px;
  padding:18px 20px;box-shadow:var(--shadow);display:flex;flex-direction:column;gap:14px}
.card.s0{border-top:3px solid var(--s0)}
.card.bench{border-top:3px solid var(--muted);border-style:dashed solid solid solid}
.card-note{margin-top:10px;font-size:12px;line-height:1.5;color:var(--muted)}
.card.s1{border-top:3px solid var(--s1)}
.card.s2{border-top:3px solid var(--s2)}
.card h3{font-size:15px;font-weight:600;display:flex;align-items:center;gap:8px}
.swatch{width:9px;height:9px;border-radius:2px}
.figures{display:grid;grid-template-columns:repeat(auto-fit,minmax(88px,1fr));gap:14px}
.fig{display:flex;flex-direction:column;gap:2px}
.fig .v{font-size:21px;font-weight:600;letter-spacing:-.02em}
.fig .v.sm{font-size:17px}
.pos{color:var(--pos)} .neg{color:var(--neg)} .mut{color:var(--muted)}

/* sections */
section{background:var(--surface);border:1px solid var(--line);border-radius:12px;
  box-shadow:var(--shadow);overflow:hidden}
.sec-head{padding:16px 20px;border-bottom:1px solid var(--line);display:flex;
  justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.sec-head h2{font-size:15px;font-weight:600}
.sec-body{padding:18px 20px}
.legend{display:flex;gap:16px;flex-wrap:wrap}
.legend span{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--muted)}
.chart{width:100%;height:260px}
.chart svg{width:100%;height:100%;display:block}
.grid{stroke:var(--line);stroke-width:1;stroke-dasharray:2 5;opacity:.7}
.baseline{stroke:var(--muted);stroke-width:1;stroke-dasharray:5 4;opacity:.55}
.empty-chart{height:260px;display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:6px;color:var(--muted);background:var(--surface-2);
  border-radius:8px;border:1px dashed var(--line)}
.empty-chart span{font-size:14px;font-weight:600}
.empty-chart small{font-size:12.5px}

/* tables */
.scroll{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px;min-width:560px}
th{text-align:left;font-size:10.5px;font-weight:600;letter-spacing:.09em;
  text-transform:uppercase;color:var(--muted);padding:0 14px 9px;white-space:nowrap}
td{padding:9px 14px;border-top:1px solid var(--line);white-space:nowrap}
tbody tr:hover{background:var(--surface-2)}
.tag{display:inline-block;font-size:10.5px;font-weight:600;padding:2px 7px;
  border-radius:4px;letter-spacing:.03em}
.tag.long{background:var(--pos-bg);color:var(--pos)}
.tag.short{background:var(--neg-bg);color:var(--neg)}
.tag.chop{background:var(--surface-2);color:var(--muted);border:1px solid var(--line)}
.tag.wait{background:var(--surface-2);color:var(--muted);border:1px solid var(--line)}
.empty-row{padding:26px 20px;text-align:center;color:var(--muted);font-size:13.5px}

footer{color:var(--muted);font-size:12.5px;text-align:center;padding-top:6px;
  max-width:70ch;margin:0 auto;line-height:1.6}
@media (max-width:600px){
  body{padding:20px 14px 48px} h1{font-size:24px}
  .stamp{text-align:left;align-items:flex-start}
}
"""


def fmt_money(v):
    return f"${v:,.2f}"


def fmt_pct(v):
    return f"{v:+.2f}%"


def cls_for(v):
    return "pos" if v > 0 else ("neg" if v < 0 else "mut")



# The chart script is a plain string, not an f-string: it is full of JavaScript
# braces and doubling every one of them to survive .format() was the kind of
# thing that breaks silently.
CHART_JS = """  <script>
  const HIST = __HIST__;
  const sel = document.getElementById('symsel');
  const svg = document.getElementById('bigchart');

  function draw(sym) {
    const pts = HIST[sym] || [];
    svg.innerHTML = '';
    if (pts.length < 3) return;
    const W = 900, H = 320, pad = 14;
    const lo = Math.min(...pts), hi = Math.max(...pts), span = (hi - lo) || 1;
    const x = i => pad + (W - 2 * pad) * (i / (pts.length - 1));
    const y = v => pad + (H - 2 * pad) * (1 - (v - lo) / span);
    const line = pts.map((v, i) => x(i).toFixed(0) + ',' + y(v).toFixed(1)).join(' ');
    const up = pts[pts.length - 1] >= pts[0];
    const col = up ? 'var(--pos)' : 'var(--neg)';
    const ns = 'http://www.w3.org/2000/svg';
    const poly = document.createElementNS(ns, 'polygon');
    poly.setAttribute('points', pad + ',' + (H - pad) + ' ' + line + ' ' + (W - pad) + ',' + (H - pad));
    poly.setAttribute('fill', col); poly.setAttribute('fill-opacity', '0.10');
    svg.appendChild(poly);
    const pl = document.createElementNS(ns, 'polyline');
    pl.setAttribute('points', line); pl.setAttribute('fill', 'none');
    pl.setAttribute('stroke', col); pl.setAttribute('stroke-width', '2');
    pl.setAttribute('stroke-linejoin', 'round');
    svg.appendChild(pl);
  }

  // Try the real widget. If the host blocks external scripts (every artifact
  // page does) the error handler fires and the native chart simply stays.
  let tvReady = false;
  function mountTV(sym) {
    if (!tvReady) return;
    document.getElementById('tv').innerHTML = '';
    try {
      new TradingView.widget({
        container_id: 'tv', symbol: 'BINANCE:' + sym, interval: '60',
        autosize: false, width: '100%', height: 380, theme:
          (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'),
        style: '1', locale: 'en', hide_side_toolbar: true, allow_symbol_change: false,
      });
      document.getElementById('fallback').style.display = 'none';
    } catch (e) { tvReady = false; }
  }

  const tag = document.createElement('script');
  tag.src = 'https://s3.tradingview.com/tv.js';
  tag.onload = () => { tvReady = true; mountTV(sel.value); };
  tag.onerror = () => {
    document.getElementById('cnote').textContent =
      'TradingView is blocked on this host, so the chart is drawn here from the '
      + 'same Binance hourly candles the books trade on.';
  };
  document.head.appendChild(tag);

  sel.addEventListener('change', () => { draw(sel.value); mountTV(sel.value); });
  draw(sel.value);
  </script>
"""


# Every term that appears anywhere on the site, in plain language. Grouped the
# way someone reads the pages rather than alphabetically: you meet them in this
# order.
GLOSSARY = [
    ("Your money", [
        ("Portfolio net worth",
         "Everything the two strategies are worth right now: cash plus what the "
         "open positions would fetch if they closed at the current price. This "
         "is the number that answers 'how am I doing'. It starts at $20,000 — "
         "$10,000 for each strategy."),
        ("Cash",
         "Money not currently in a trade. It only changes when a position "
         "closes. Opening a trade moves value from cash into the position, it "
         "does not spend it."),
        ("In open trades / Unrealised",
         "Profit or loss on positions still open. Real in the sense that it is "
         "what you would get if everything closed now; not real in the sense "
         "that it can vanish before that happens. Nothing is booked until a "
         "position closes."),
        ("Realised P&amp;L",
         "Profit or loss from trades that have actually closed. The part that "
         "cannot be taken back."),
        ("Round trip",
         "One complete trade: an entry and its matching exit. Two orders, one "
         "round trip. The statistics count round trips, not orders."),
    ]),
    ("Positions and trades", [
        ("Long",
         "A bet the price goes up. Buy at $100, sell at $110, you made $10."),
        ("Short",
         "A bet the price goes down. You sell first at $100 and buy back at "
         "$90, keeping the $10. Bob trades both, because crypto falls as often "
         "as it rises."),
        ("Entry", "The price a position was opened at."),
        ("Stop / stop-loss",
         "A price that closes the trade at a loss to stop it getting worse. Bob "
         "puts it 2.5 ATR from entry, so a jumpy coin gets a wider stop than a "
         "calm one: same risk, different distance."),
        ("Target / take-profit",
         "A price that closes the trade at a profit. Bob sets it 2.5 times as "
         "far away as the stop, which is what '2.5R' means."),
        ("Open position",
         "A trade that has been entered and not yet exited."),
        ("Signal-flatten",
         "An exit that happens because the reason for the trade disappeared — "
         "the trend died or reversed — rather than because the stop or target "
         "was hit."),
    ]),
    ("The order log", [
        ("ENTRY", "An order that opened a position."),
        ("EXIT",
         "An order that closed one. Its P&amp;L column is what that trade made "
         "or lost."),
        ("Book",
         "Which of the seven accounts placed the order. Two run the real "
         "strategies; five enter at random as a comparison."),
        ("Reason",
         "Why a position closed: <em>take-profit</em>, <em>stop-loss</em>, or "
         "<em>signal-flatten</em>."),
    ]),
    ("What the strategies look at", [
        ("Candle",
         "One hour of price as four numbers: where it opened, the highest it "
         "went, the lowest, and where it closed. Bob only acts on candles that "
         "have finished, never a half-formed one."),
        ("EMA100 baseline",
         "The average price over the last 100 hours, weighted towards recent "
         "ones. Above it is an uptrend, below it a downtrend. The first thing "
         "every strategy checks."),
        ("Confluence",
         "Requiring several independent indicators to agree before trading. Any "
         "one indicator fires constantly and most of it is noise; demanding "
         "agreement cuts it down to something worth acting on."),
        ("RSI",
         "Relative Strength Index, 0–100. How one-sided recent moves have been. "
         "Above 55 counts as bullish here, below 45 bearish."),
        ("MACD",
         "Compares a fast and a slow average of price. Fast above slow means "
         "momentum is upward."),
        ("Stochastic",
         "0–100. Where the price sits inside its recent high-to-low range. Near "
         "100 means it is closing at the top of its range."),
        ("MFI",
         "Money Flow Index, 0–100. Like RSI but weighted by volume, so it asks "
         "whether the money moving in matches the price moving up."),
        ("ADX",
         "0–100. How strongly a market is trending, in either direction. Below "
         "the threshold the market is chopping sideways and Bob refuses to "
         "trade it — chop is where trend systems bleed."),
        ("ATR",
         "Average True Range: how far this market typically moves in an hour. "
         "Position size and stop distance are both scaled by it."),
        ("Reads: long / short / chop / wait",
         "What a strategy currently sees. <em>chop</em> means the trend filter "
         "has ruled the market out; <em>wait</em> means it is trending but the "
         "indicators do not agree yet."),
    ]),
    ("How risk is controlled", [
        ("R / 2.5R",
         "R is the amount risked on a trade. A 2.5R target aims to make two and "
         "a half times what it risks — which is why Bob can win well under half "
         "his trades and still come out ahead."),
        ("Risk per trade",
         "%s%% of the account on any single position. Equal risk everywhere, so "
         "a volatile coin takes a smaller position than a calm one for the same "
         "money at stake."),
        ("Notional",
         "The full size of a position. Capped at 1/%s of the account so nothing "
         "can dominate."),
        ("Max per side",
         "At most %s longs and %s shorts at once. Crypto markets move together, "
         "so twenty longs are closer to one big bet than twenty separate ones."),
        ("Drawdown",
         "How far the account has fallen from its highest point. Bob halts at "
         "%s%%."),
        ("Halt",
         "Trading stops for %sh after %s losses in a row, or that drawdown. "
         "Position size also halves after %s losses in a row, before the full "
         "halt."),
        ("Commission and slippage",
         "%s%% and %s%% charged on every side of every trade. Slippage is the "
         "gap between the price you asked for and the price you got. Both count "
         "against Bob, so the numbers are not flattering themselves."),
    ]),
    ("Judging whether it works", [
        ("Buy &amp; hold",
         "What you would have if you simply bought all 32 coins in equal "
         "amounts at the start and did nothing. The honest thing to compare "
         "against: a strategy below this line lost money by being clever."),
        ("Control / random entry",
         "Five accounts that enter at random, with exactly the same markets, "
         "stops, targets and sizing as the real strategies. The only difference "
         "is that their entries carry no information. If the strategies cannot "
         "beat these, the indicators are doing nothing and any profit came from "
         "the risk rules instead."),
        ("Why five controls",
         "One random account is one roll of the dice and can look brilliant by "
         "luck. Five give a range to place the real strategies inside."),
        ("Per trade",
         "Average profit or loss per closed trade. The fairest way to compare "
         "accounts that trade at different rates."),
        ("Win rate",
         "Share of closed trades that made money. On its own it says very "
         "little: a 2.5R target behind an ATR stop wins about 28%% of the time "
         "on pure chance, so 30%% is not evidence of skill."),
        ("t-statistic",
         "How confident you can be that a result is not luck. Above 2 is the "
         "usual bar. It shows as a dash until there are at least %s closed "
         "trades, because on two or three it produces enormous numbers that "
         "mean nothing."),
        ("Evidence collected",
         "Progress towards roughly %s closed trades per strategy, about where "
         "returns start to be distinguishable from luck. Until then the "
         "standings are entertainment."),
        ("Paper trading",
         "Simulated money on real prices. No exchange account, no orders, "
         "nothing at risk. The prices and the costs are real; the money is not."),
    ]),
]

_GLOSSARY_NUMBERS = (
    pt.RISK_PCT, pt.MAX_CONCURRENT, pt.MAX_PER_SIDE, pt.MAX_PER_SIDE,
    pt.MAX_DD_PCT, pt.HALT_COOLDOWN_HOURS, pt.MAX_CONSEC_LOSS,
    pt.SOFT_LOSS_STREAK, pt.COMMISSION_PCT, pt.SLIPPAGE_PCT,
    pt.MIN_T_TRADES, SIGNIFICANCE_TARGET,
)


def render_glossary():
    """Definitions, with every number pulled from the live config rather than
    typed in — a glossary that drifts from the code is worse than none."""
    nums = list(_GLOSSARY_NUMBERS)
    out = []
    for group, items in GLOSSARY:
        rows = []
        for term, text in items:
            n = text.count("%s")
            if n:
                text = text % tuple(nums[:n])
                del nums[:n]
            text = text.replace("%%", "%")
            rows.append('<div class="gl"><dt>' + term + "</dt><dd>" + text
                        + "</dd></div>")
        out.append('\n  <section>\n    <div class="sec-head"><h2>' + group
                   + '</h2></div>\n    <div class="sec-body">'
                   + '<dl class="glossary">' + "".join(rows)
                   + "</dl></div>\n  </section>")
    assert not nums, "glossary placeholders and numbers are out of step"
    return "".join(out)


# Live prices in the browser. Bob re-renders the page every ten minutes and a
# GitHub Pages rebuild is capped near ten an hour, so the file itself cannot get
# fresher than that. The browser has no such limit: one request to Binance
# returns every symbol at once, so the page re-prices its own positions every
# few seconds without a rebuild, without touching Bob, and at no cost.
#
# Blocked hosts (every artifact page) simply keep the server-rendered numbers —
# the fetch fails, the catch fires, and the "live" tag never appears.
TICKER_JS = """
  <script>
  (function () {
    const CASH = __CASH__;
    const cards = [...document.querySelectorAll('.holding[data-sym]')];
    // Ask for the held symbols only. The unfiltered endpoint returns ~3,700
    // symbols and 150KB; every five seconds that is 100MB an hour down someone's
    // phone to read a dozen prices.
    const syms = [...new Set(cards.map(c => c.dataset.sym))];
    const URL_ = 'https://data-api.binance.vision/api/v3/ticker/price?symbols='
      + encodeURIComponent(JSON.stringify(syms));
    const nw = document.querySelector('.js-networth');
    const un = document.querySelector('.js-unreal');
    const money = n => (n < 0 ? '-' : '') + '$' +
      Math.abs(n).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    const signed = n => (n >= 0 ? '+' : '') +
      n.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    const klass = (el, n) => {
      el.classList.remove('pos', 'neg');
      el.classList.add(n >= 0 ? 'pos' : 'neg');
    };

    async function tick() {
      let map;
      try {
        const r = await fetch(URL_);
        if (!r.ok) throw new Error(r.status);
        map = new Map((await r.json()).map(x => [x.symbol, parseFloat(x.price)]));
      } catch (e) { return false; }

      let unreal = 0;
      for (const c of cards) {
        const px = map.get(c.dataset.sym);
        if (!px) continue;
        const entry = parseFloat(c.dataset.entry), qty = parseFloat(c.dataset.qty);
        const d = c.dataset.side === 'long' ? px - entry : entry - px;
        const pnl = d * qty;
        unreal += pnl;
        const el = c.querySelector('.js-pnl');
        if (el) {
          el.firstChild.nodeValue = signed(pnl) + ' ';
          const pct = el.querySelector('span');
          if (pct) pct.textContent = signed(d / entry * 100) + '%';
          klass(el, pnl);
        }
        const now = c.querySelector('.js-now');
        if (now) now.textContent = 'Now ' + px.toLocaleString('en-US',
          {minimumFractionDigits: 4, maximumFractionDigits: 4});
      }
      if (un) { un.textContent = money(unreal); klass(un, unreal); }
      if (nw) nw.textContent = money(CASH + unreal);
      const stamp = document.getElementById('pricedat');
      if (stamp) stamp.textContent = 'Prices ' +
        new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit',
                                           second: '2-digit'});
      return true;
    }

    // GitHub Pages serves cache-control: max-age=600, so a browser will happily
    // show a ten-minute-old page and never ask for a newer one -- stacked on
    // Bob's ten-minute render that is up to twenty minutes stale. Poll the page
    // itself past the cache and reload only when the build stamp has actually
    // moved, so nothing flickers when there is nothing new.
    const BUILT = (document.getElementById('built') || {}).textContent || '';
    async function checkBuild() {
      try {
        const r = await fetch(location.pathname + '?_=' + Date.now(),
                              {cache: 'no-store'});
        if (!r.ok) return;
        const t = (await r.text()).match(/id="built"[^>]*>([^<]*)</);
        if (t && t[1] && t[1] !== BUILT) location.reload();
      } catch (e) { /* offline or blocked: keep showing what we have */ }
    }

    // Always on: it is the only thing that gets a new build past the cache,
    // and it must survive a blocked price feed and an empty book alike.
    setInterval(checkBuild, 60000);

    if (cards.length) {
      tick().then(ok => {
        if (!ok) return;          // host blocks the fetch: keep what was rendered
        const pill = document.querySelector('.pill');
        if (pill) pill.lastChild.nodeValue = 'Live prices';
        setInterval(tick, 5000);
      });
    }
  })();
  </script>
"""

NAV = (("index.html", "Portfolio"), ("orders.html", "Orders"),
       ("markets.html", "Markets"), ("research.html", "Research"),
       ("glossary.html", "Glossary"))


def shell(title, active, body, meta, subtitle, script=""):
    """Wrap sections in the common page. `active` is the nav href to highlight;
    pass None for the single-file build, which has no other pages to link to."""
    if active is None:
        nav = ""
    else:
        nav = '<nav class="nav">' + "".join(
            f'<a href="{href}"{" class=\'on\'" if href == active else ""}>'
            f'{html.escape(label)}</a>' for href, label in NAV) + "</nav>"
    return f"""<title>{html.escape(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Serif:wght@600&display=swap">
<style>{CSS}</style>
<div class="wrap">

  <header>
    <div>
      <h1>Bob</h1>
      <p class="tagline">{subtitle}</p>
    </div>
    <div class="stamp">
      {meta['pill']}
      <span class="lbl" id="built">Page built {meta['generated']:%d %b %Y · %H:%M} UTC</span>
      <span class="lbl" id="pricedat"></span>
    </div>
  </header>
  {nav}
{body}
  <footer>Simulated trading only &mdash; no orders are placed and no real money is
    at risk. Each strategy runs one {fmt_money(pt.INITIAL_CAPITAL)} account across
    {len(pt.SYMBOLS)} markets on {pt.INTERVAL} candles, risking {pt.RISK_PCT}% of
    equity per position (capped at 1/{pt.MAX_CONCURRENT} notional, max
    {pt.MAX_PER_SIDE} per side) behind a {pt.ATR_MULT_SL}&times; ATR stop with a
    {pt.RR}R target. The book halts for {pt.HALT_COOLDOWN_HOURS}h after
    {pt.MAX_CONSEC_LOSS} consecutive losses or a {pt.MAX_DD_PCT:.0f}% drawdown.</footer>
</div>
{script}
"""



def build_html(state, trades, live, generated, bench=None, orders=None):
    stats = strategy_stats(trades, state, live)
    orders = orders or []
    totals = portfolio_totals(stats, orders)
    curves = equity_curves(trades, state)
    total_trades = len(trades)
    # The evidence meter tracks the STRATEGIES only. Counting the five control
    # books here would fill it more than three times too fast and claim a
    # sample that does not exist.
    real_trades = sum(1 for t in trades if t["strategy"] in REAL)
    real_target = SIGNIFICANCE_TARGET * len(REAL)

    # --- staleness: has the feed advanced recently? ---
    # last_close_ms is a {symbol: ms} map per account — flatten before comparing
    newest = max((ms for acct in state.values()
                  for ms in acct.get("last_close_ms", {}).values()), default=0)
    age_h = (generated.timestamp() * 1000 - newest) / 3_600_000 if newest else 999
    live_ok = age_h < 2.5
    pill = ('<span class="pill live"><span class="dot"></span>Running</span>' if live_ok
            else '<span class="pill stale"><span class="dot"></span>No new candles</span>')

    # --- verdict copy ---
    # The question this dashboard answers is NOT "which strategy is ahead".
    # Two books entering on indicators are compared against a third entering at
    # random with an identical risk model, and against simply holding the same
    # 32 markets. A strategy that beats neither has demonstrated nothing,
    # whatever its return says.
    ranked = sorted(((k, v) for k, v in stats.items() if k in REAL),
                    key=lambda kv: kv[1]["avg_trade"], reverse=True)
    lead = ranked[0]
    ctrl_means = sorted(stats[k]["avg_trade"] for k in CONTROLS
                        if stats.get(k, {}).get("trades", 0) >= 2)
    ctrl_n = sum(stats.get(k, {}).get("trades", 0) for k in CONTROLS)
    bench_ret = (bench / pt.INITIAL_CAPITAL - 1) * 100 if bench else None
    min_real = min(s_["trades"] for _, s_ in ranked)
    beaten = (sum(1 for c in ctrl_means if lead[1]["avg_trade"] > c)
              if ctrl_means else 0)

    if total_trades == 0:
        headline = "Waiting for the first trade"
        body = (f"Seven books are loaded across {len(pt.SYMBOLS)} markets: two "
                f"entering on indicator confluence and {len(CONTROLS)} entering at "
                "random, all sharing one risk model. Entries fire only on a closed "
                "candle.")
    elif min_real < 10 or ctrl_n < 10:
        headline = "Too early to read"
        body = (f"{real_trades} strategy trades so far. At this sample the standings are "
                "noise — a single trade reorders them. The number worth watching "
                "is the trade count, not the returns.")
    elif not ctrl_means:
        headline = f"{LABEL[lead[0]]} leads on expectancy"
        body = "Not enough closed trades in the control books to say if that means anything."
    else:
        clears = beaten == len(ctrl_means)
        headline = (f"{LABEL[lead[0]]} beats every random book"
                    if clears else "No book has beaten random entry")
        body = (
            f"The best strategy averages {fmt_money(lead[1]['avg_trade'])} per trade. "
            f"{len(ctrl_means)} coin-flip books on the same markets, with the same "
            f"stops, targets and sizing, range from {fmt_money(ctrl_means[0])} to "
            f"{fmt_money(ctrl_means[-1])} — it beats {beaten} of them. ")
        body += ("Sitting above the whole range is the first real sign the entry "
                 "signal does something."
                 if clears else
                 "Landing inside that range means the indicators are not "
                 "distinguishable from chance, and whatever the books have earned "
                 "came from the risk model rather than the signal.")
        if bench_ret is not None:
            best_ret = max(s_["return_pct"] for _, s_ in ranked)
            body += (f" Holding the same basket returned {fmt_pct(bench_ret)} over "
                     f"the same window, against {fmt_pct(best_ret)} for the best book.")

    pct_done = min(real_trades / real_target * 100, 100)

    # --- strategy cards ---
    cards = []
    cs = [stats[k] for k in CONTROLS if k in stats]
    if cs:
        c_trades = sum(c["trades"] for c in cs)
        c_means = sorted(c["avg_trade"] for c in cs)
        # mean, not median: early on most books sit at exactly the starting
        # figure, so the median is whichever one has not traded yet
        c_eq = sum(c["equity"] for c in cs) / len(cs)
        c_open = sum(len(c["positions"]) for c in cs)
        c_wins = sum(c["wins"] for c in cs)
        cards.append(f"""
      <div class="card s0">
        <h3><span class="swatch" style="background:var(--s0)"></span>Control &times;{len(cs)} &middot; random entry</h3>
        <div class="figures">
          <div class="fig"><span class="lbl">Mean equity</span>
            <span class="v num">{fmt_money(c_eq)}</span></div>
          <div class="fig"><span class="lbl">Mean return</span>
            <span class="v num {cls_for((c_eq / pt.INITIAL_CAPITAL - 1) * 100)}">{fmt_pct((c_eq / pt.INITIAL_CAPITAL - 1) * 100)}</span></div>
          <div class="fig"><span class="lbl">Closed</span>
            <span class="v num sm">{c_trades}</span></div>
          <div class="fig"><span class="lbl">Open</span>
            <span class="v num sm">{c_open} <span class="mut" style="font-size:12px">pos</span></span></div>
          <div class="fig"><span class="lbl">Win rate</span>
            <span class="v num sm">{c_wins / c_trades * 100 if c_trades else 0:.0f}%</span></div>
          <div class="fig"><span class="lbl">Per trade</span>
            <span class="v num sm">{fmt_money(c_means[0]) if c_means else '—'} to {fmt_money(c_means[-1]) if c_means else '—'}</span></div>
        </div>
        <p class="card-note">Coin-flip entries, same markets and same risk model.
          A 2.5R target behind an ATR stop already wins about 28% of the time on
          chance alone, so this is the line a real strategy has to clear.</p>
      </div>""")

    for strat in REAL:
        s = stats[strat]
        key = SERIES[strat]
        t_txt = f"{s['t_stat']:.2f}" if s["t_stat"] is not None else "—"
        cards.append(f"""
      <div class="card {key}">
        <h3><span class="swatch" style="background:var(--{key})"></span>{html.escape(LABEL[strat])}</h3>
        <div class="figures">
          <div class="fig"><span class="lbl">Equity</span>
            <span class="v num">{fmt_money(s['equity'])}</span></div>
          <div class="fig"><span class="lbl">Return</span>
            <span class="v num {cls_for(s['return_pct'])}">{fmt_pct(s['return_pct'])}</span></div>
          <div class="fig"><span class="lbl">Trades</span>
            <span class="v num sm">{s['trades']}</span></div>
          <div class="fig"><span class="lbl">Win rate</span>
            <span class="v num sm">{s['win_rate']:.0f}%</span></div>
          <div class="fig"><span class="lbl">Open</span>
            <span class="v num sm">{len(s['positions'])} <span class="mut" style="font-size:12px">pos</span></span></div>
          <div class="fig"><span class="lbl">Unrealised</span>
            <span class="v num sm {cls_for(s['unrealised'])}">{fmt_money(s['unrealised'])}</span></div>
          <div class="fig"><span class="lbl">t-stat</span>
            <span class="v num sm mut">{t_txt}</span></div>
        </div>
      </div>""")

    if bench:
        b_ret = (bench / pt.INITIAL_CAPITAL - 1) * 100
        best = max((s_["return_pct"] for k, s_ in stats.items() if k in REAL),
                   default=0.0)
        cards.append(f"""
      <div class="card bench">
        <h3><span class="swatch" style="background:var(--muted)"></span>Buy &amp; hold</h3>
        <div class="figures">
          <div class="fig"><span class="lbl">Equity</span>
            <span class="v num">{fmt_money(bench)}</span></div>
          <div class="fig"><span class="lbl">Return</span>
            <span class="v num {cls_for(b_ret)}">{fmt_pct(b_ret)}</span></div>
          <div class="fig"><span class="lbl">Best book vs hold</span>
            <span class="v num sm {cls_for(best - b_ret)}">{fmt_pct(best - b_ret)}</span></div>
          <div class="fig"><span class="lbl">Basket</span>
            <span class="v num sm mut">{len(pt.SYMBOLS)} equal</span></div>
        </div>
        <p class="card-note">Doing nothing but holding the same markets, from the
          same start. Any book below this line lost money by being clever.</p>
      </div>""")

    # --- open positions, as cards with a price chart each ---
    # Strategy books only. The five control books hold their own positions, but
    # they are instrumentation, not the user's portfolio — their count is noted
    # beside the heading and their results live in the comparison below.
    pos_cards = []
    for strat, st in ((k, stats[k]) for k in REAL if k in stats):
        for sym, pos in sorted(st["positions"].items()):
            d = live.get(sym, {})
            price = d.get("now") or d.get("price") or pos.get("last") or pos["entry"]
            delta = ((price - pos["entry"]) if pos["side"] == "long"
                     else (pos["entry"] - price))
            pnl = delta * pos["qty"]
            pct = delta / pos["entry"] * 100 if pos["entry"] else 0.0
            tv = html.escape(f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym}")
            # distance travelled from stop to target, as a progress bar
            lo_, hi_ = (pos["sl"], pos["tp"]) if pos["side"] == "long" else (pos["tp"], pos["sl"])
            frac = 0.0 if hi_ == lo_ else max(0.0, min(1.0, (price - lo_) / (hi_ - lo_)))
            if pos["side"] == "short":
                frac = 1 - frac
            pos_cards.append(f"""
      <div class="holding" data-sym="{html.escape(sym)}" data-side="{pos['side']}"
           data-entry="{pos['entry']:.10g}" data-qty="{pos['qty']:.10g}">
        <div class="pos-top">
          <div>
            <span class="sym">{html.escape(sym.replace('USDT', ''))}</span>
            <span class="tag {pos['side']}">{pos['side']}</span>
          </div>
          <span class="num {cls_for(pnl)} pos-pnl js-pnl">{pnl:+,.2f}
            <span class="mut" style="font-size:11px">{pct:+.2f}%</span></span>
        </div>
        <div class="pos-book">{html.escape(SHORT.get(strat, strat))}</div>
        {render_spark(d.get("hist"), pos["entry"], pos["sl"], pos["tp"], pos["side"])}
        <div class="pos-bar"><div class="pos-bar-fill" style="width:{frac * 100:.0f}%"></div></div>
        <div class="pos-legs">
          <span><i class="k sl"></i>Stop {pos['sl']:,.4f}</span>
          <span><i class="k en"></i>Entry {pos['entry']:,.4f}</span>
          <span><i class="k tp"></i>Target {pos['tp']:,.4f}</span>
        </div>
        <div class="pos-foot">
          <span class="num js-now">Now {price:,.4f}</span>
          <a href="{tv}" target="_blank" rel="noopener">TradingView &#8599;</a>
        </div>
      </div>""")
    if not pos_cards:
        pos_cards = ['<div class="empty-row">Neither strategy is holding anything '
                     'right now. Positions appear here the moment one enters.</div>']

    # --- market chart: TradingView where the host allows it ---
    # Artifact pages run under a CSP that blocks every external host, so the
    # widget cannot load there. Rather than ship an empty box, the page draws
    # the same Binance candles itself and only swaps in TradingView if the
    # script actually arrives. Self-hosted copies get the real widget.
    held = sorted({sym for k in REAL if k in stats for sym in stats[k]["positions"]})
    if not held:
        held = sorted(list(live)[:12])
    chart_data = {sym: (live.get(sym, {}).get("hist") or []) for sym in held}
    chart_json = json.dumps({k: v for k, v in chart_data.items() if v})
    chart_opts = "".join(
        f'<option value="{html.escape(s_)}">{html.escape(s_.replace("USDT", ""))}</option>'
        for s_ in held)

    # --- order book: entries and exits, newest first ---
    order_rows = "".join(f"""
        <tr><td class="num mut">{html.escape(o['time'][:16].replace('T', ' '))}</td>
        <td><span class="ev {o['event'].lower()}">{o['event']}</span></td>
        <td class="num">{html.escape(o['symbol'].replace('USDT', ''))}</td>
        <td><span class="tag {o['side']}">{o['side']}</span></td>
        <td class="num">{o['price']:,.4f}</td>
        <td class="mut">{html.escape(SHORT.get(o['strategy'], o['strategy']))}</td>
        <td class="mut">{html.escape(o['reason'] or '—')}</td>
        <td class="num {cls_for(o['pnl']) if o['pnl'] is not None else 'mut'}">{
            f"{o['pnl']:+,.2f}" if o['pnl'] is not None else '—'}</td></tr>"""
        for o in orders)
    if not order_rows:
        order_rows = ('<tr><td colspan="8" class="empty-row">No orders yet.</td></tr>')


    # --- live signals ---
    sig_rows = []
    for sym in pt.SYMBOLS:
        d = live.get(sym)
        if not d:
            continue
        bias = "above" if d["price"] > d["ema"] else "below"
        reads = "".join(
            f'<td><span class="tag {d["reads"][s2]}">{d["reads"][s2]}</span></td>'
            for s2 in SHOWN)
        sig_rows.append(f"""
        <tr><td class="num">{html.escape(sym.replace("USDT", ""))}</td>
        <td class="num">{d['price']:,.6g}</td>
        <td class="mut">{bias} EMA100</td>
        <td class="num">{d['rsi']:.0f}</td><td class="num">{d['stoch']:.0f}</td>
        <td class="num">{d['mfi']:.0f}</td><td class="num">{d['adx']:.0f}</td>
        {reads}</tr>""")
    if not sig_rows:
        sig_rows = ['<tr><td colspan="9" class="empty-row">Waiting for the '
                    'first price fetch.</td></tr>']


    legend = "".join(
        f'<span><span class="swatch" style="background:var(--{SERIES[s]})"></span>'
        f'{html.escape(LABEL[s])}</span>' for s in SHOWN)

    # ---- sections, so pages can be assembled from them ----
    sec = {}

    sec["summary"] = f"""
  <div class="summary">
    <div class="big">
      <span class="lbl">Portfolio net worth</span>
      <span class="v num js-networth">{fmt_money(totals['net_worth'])}</span>
      <span class="num {cls_for(totals['net_worth'] - totals['base'])} sub">
        {fmt_money(totals['net_worth'] - totals['base'])} ({fmt_pct((totals['net_worth'] / totals['base'] - 1) * 100)})
        since {fmt_money(totals['base'])} start</span>
    </div>
    <div class="tiles">
      <div class="tile"><span class="lbl">Cash</span>
        <span class="v num">{fmt_money(totals['cash'])}</span>
        <span class="sub mut">settled, not in a trade</span></div>
      <div class="tile"><span class="lbl">In open trades</span>
        <span class="v num js-unreal {cls_for(totals['unrealised'])}">{fmt_money(totals['unrealised'])}</span>
        <span class="sub mut">{totals['open']} position{'' if totals['open'] == 1 else 's'} live</span></div>
      <div class="tile"><span class="lbl">Orders placed</span>
        <span class="v num">{totals['orders']}</span>
        <span class="sub mut">{totals['closed']} round trips closed</span></div>
      <div class="tile"><span class="lbl">Win rate</span>
        <span class="v num">{(totals['wins'] / totals['closed'] * 100) if totals['closed'] else 0:.0f}%</span>
        <span class="sub mut">{totals['wins']}W / {totals['closed'] - totals['wins']}L</span></div>
    </div>
  </div>"""

    sec["positions"] = f"""
  <section>
    <div class="sec-head"><h2>Open positions</h2>
      <span class="lbl">{totals['open']} held by the strategies &middot;
        {sum(len(stats[k]['positions']) for k in CONTROLS if k in stats)} more in the control books</span></div>
    <div class="sec-body"><div class="pos-grid">{"".join(pos_cards)}</div></div>
  </section>"""

    sec["chart"] = f"""
  <section>
    <div class="sec-head"><h2>Market chart</h2>
      <select id="symsel" aria-label="Choose a market">{chart_opts}</select></div>
    <div class="sec-body">
      <div id="tv"></div>
      <div id="fallback"><svg id="bigchart" viewBox="0 0 900 320"
        preserveAspectRatio="none" role="img" aria-label="Price history"></svg>
        <p class="chart-note" id="cnote">Drawn from the same Binance hourly candles
          the books trade on.</p></div>
    </div>
  </section>"""

    sec["orders"] = f"""
  <section>
    <div class="sec-head"><h2>Recent orders</h2>
      <span class="lbl">{len(orders)} shown, newest first</span></div>
    <div class="scroll"><table>
      <thead><tr><th>Time</th><th>Event</th><th>Market</th><th>Side</th>
        <th>Price</th><th>Book</th><th>Reason</th><th>P&amp;L</th></tr></thead>
      <tbody>{order_rows}</tbody></table></div>
  </section>"""

    sec["equity"] = f"""
  <section>
    <div class="sec-head"><h2>Equity curve</h2><div class="legend">{legend}</div></div>
    <div class="sec-body"><div class="chart">{render_chart(curves)}</div></div>
  </section>"""

    sec["verdict"] = f"""
  <div class="verdict">
    <div class="verdict-head"><h2>{html.escape(headline)}</h2></div>
    <p>{html.escape(body)}</p>
    <div class="meter">
      <div class="meter-row">
        <span class="lbl">Evidence collected</span>
        <span class="num" style="font-size:13px">{real_trades} of ~{real_target} strategy trades</span>
      </div>
      <div class="meter-track"><div class="meter-fill" style="width:{pct_done:.1f}%"></div></div>
      <div class="meter-row">
        <span class="mut" style="font-size:12.5px">Roughly {SIGNIFICANCE_TARGET} trades
          per strategy before returns can be told apart from luck.</span>
      </div>
    </div>
  </div>"""

    sec["books"] = f'\n  <div class="grid2">{"".join(cards)}</div>'

    sec["signals"] = f"""
  <section>
    <div class="sec-head"><h2>What each strategy sees right now</h2></div>
    <div class="scroll"><table>
      <thead><tr><th>Market</th><th>Price</th><th>Trend</th><th>RSI</th><th>Stoch</th>
        <th>MFI</th><th>ADX</th>
        {"".join(f'<th>{html.escape(LABEL[s].split(chr(183))[0].strip())}</th>' for s in SHOWN)}
      </tr></thead>
      <tbody>{"".join(sig_rows)}</tbody></table></div>
  </section>"""

    sec["script"] = CHART_JS.replace("__HIST__", chart_json)
    sec["ticker"] = TICKER_JS.replace("__CASH__", f"{totals['cash']:.4f}")

    return sec, {"pill": pill, "generated": generated}


SUB = {
    "index.html": "Simulated money, live crypto prices. What the two strategies "
                  "are holding right now and what the account is worth.",
    "orders.html": "Every entry and exit the books have made, newest first.",
    "markets.html": "Price charts and what each strategy currently reads in all "
                    "{n} markets.",
    "research.html": "Does either strategy actually beat random entry? Two "
                     "indicator books against {c} coin-flip books and buy-and-hold.",
    "glossary.html": "What every number and label on this site actually means.",
}


def generate(out_file=OUT_FILE, live=None, fetch_live=True, bench=None, out_dir=None):
    """Render the site. Writes the multi-page build into `out_dir` (default: the
    directory of `out_file`) and a single-file build to `out_file` for hosts that
    can only serve one page, which is what a Claude artifact is."""
    state = read_state()
    trades = read_trades()
    if bench is None:
        b = pt.Benchmark()
        try:
            with open(pt.STATE_FILE) as f:
                b.load(json.load(f).get("benchmark", {}))
        except (OSError, json.JSONDecodeError):
            pass
        bench = b.equity()
    if live is None:
        live = live_prices() if fetch_live else {}

    sec, meta = build_html(state, trades, live, datetime.now(timezone.utc),
                           bench=bench, orders=read_orders())
    out_dir = out_dir or (os.path.dirname(os.path.abspath(out_file)) or ".")

    def sub(name):
        return SUB[name].format(n=len(pt.SYMBOLS), c=len(CONTROLS))

    pages = {
        "index.html": (sec["summary"] + sec["positions"], sec["ticker"]),
        "orders.html": (sec["orders"], sec["ticker"]),
        "markets.html": (sec["chart"] + sec["signals"],
                         sec["script"] + sec["ticker"]),
        "research.html": (sec["verdict"] + sec["books"] + sec["equity"],
                          sec["ticker"]),
        "glossary.html": (render_glossary(), sec["ticker"]),
    }
    for name, (body, script) in pages.items():
        with open(os.path.join(out_dir, name), "w") as f:
            f.write(shell(f"Bob · {dict(NAV)[name]}", name, body, meta,
                          sub(name), script))

    # Single file: every section, no nav, for a one-page host.
    one = (sec["summary"] + sec["positions"] + sec["chart"]
           + sec["orders"] + sec["verdict"] + sec["books"] + sec["equity"]
           + sec["signals"]
           + render_glossary())
    with open(out_file, "w") as f:
        f.write(shell("Bob", None, one, meta, sub("index.html"),
                      sec["script"] + sec["ticker"]))
    return out_file, len(trades), len(state)


if __name__ == "__main__":
    path, n_trades, n_books = generate()
    print(f"wrote {path} + {len(NAV)} pages — {n_books} books, "
          f"{n_trades} closed trades")
