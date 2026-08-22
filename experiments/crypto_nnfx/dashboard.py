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


def live_prices():
    """Last close per symbol, plus each strategy's current read. Best-effort."""
    out = {}
    for sym in pt.SYMBOLS:
        try:
            bars = pt.fetch_klines(sym)[:-1]
            ind = pt.compute_indicators(bars)
            i = len(bars) - 1
            reads = {}
            for name, cfg in pt.STRATEGIES.items():
                long_ok, short_ok, bull, bear, trending = cfg["signal"](ind, i, cfg)
                reads[name] = ("long" if long_ok else "short" if short_ok else
                               "chop" if not trending else "wait")
            out[sym] = {
                "price": ind["close"][i], "ema": ind["ema"][100][i],
                "rsi": ind["rsi"][i], "stoch": ind["stoch"][i], "mfi": ind["mfi"][i],
                "adx": ind["adx"][i], "atr": ind["atr"][i], "reads": reads,
                "bar_ms": bars[i]["closeTime"],
            }
        except Exception:  # noqa: BLE001 - dashboard renders fine without live data
            pass
    return out


def strategy_stats(trades, state):
    """Aggregate per strategy: equity, returns, win/loss, profit factor, t-stat."""
    stats = {}
    for strat in pt.STRATEGIES:
        acct = state.get(strat, {})
        ts = [t for t in trades if t["strategy"] == strat]
        pnls = [t["pnl"] for t in ts]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_win, gross_loss = sum(wins), abs(sum(losses))
        equity = acct.get("cash_equity", pt.INITIAL_CAPITAL)
        mean = sum(pnls) / len(pnls) if pnls else 0.0
        # Sample t-stat on mean trade P&L — the honest read on whether any
        # apparent edge is distinguishable from noise at this sample size.
        t_stat, se = None, 0.0
        if len(pnls) > 2:
            var = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
            se = math.sqrt(var / len(pnls)) if var > 0 else 0.0
            t_stat = mean / se if se > 0 else None
        stats[strat] = {
            "equity": equity, "base": pt.INITIAL_CAPITAL,
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
    for strat, key in SERIES.items():
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


def build_html(state, trades, live, generated, bench=None):
    stats = strategy_stats(trades, state)
    curves = equity_curves(trades, state)
    total_trades = len(trades)

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
        body = (f"{total_trades} trades so far. At this sample the standings are "
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

    pct_done = min(total_trades / (SIGNIFICANCE_TARGET * 2) * 100, 100)

    # --- strategy cards ---
    cards = []
    cs = [stats[k] for k in CONTROLS if k in stats]
    if cs:
        c_trades = sum(c["trades"] for c in cs)
        c_means = sorted(c["avg_trade"] for c in cs)
        c_eq = sorted(c["equity"] for c in cs)[len(cs) // 2]
        c_wins = sum(c["wins"] for c in cs)
        cards.append(f"""
      <div class="card s0">
        <h3><span class="swatch" style="background:var(--s0)"></span>Control &times;{len(cs)} &middot; random entry</h3>
        <div class="figures">
          <div class="fig"><span class="lbl">Median equity</span>
            <span class="v num">{fmt_money(c_eq)}</span></div>
          <div class="fig"><span class="lbl">Median return</span>
            <span class="v num {cls_for((c_eq / pt.INITIAL_CAPITAL - 1) * 100)}">{fmt_pct((c_eq / pt.INITIAL_CAPITAL - 1) * 100)}</span></div>
          <div class="fig"><span class="lbl">Trades</span>
            <span class="v num sm">{c_trades}</span></div>
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
        pf = ("∞" if s["profit_factor"] == float("inf")
              else f"{s['profit_factor']:.2f}" if s["trades"] else "—")
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
          <div class="fig"><span class="lbl">Profit factor</span>
            <span class="v num sm">{pf}</span></div>
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

    # --- open positions ---
    open_rows = []
    for strat, st in stats.items():
        for sym, pos in sorted(st["positions"].items()):
            price = live.get(sym, {}).get("price") or pos.get("last")
            if price:
                delta = ((price - pos["entry"]) if pos["side"] == "long"
                         else (pos["entry"] - price)) * pos["qty"]
                unreal = f'<span class="num {cls_for(delta)}">{delta:+,.2f}</span>'
                now_px = f'<span class="num">{price:,.6g}</span>'
            else:
                unreal = now_px = '<span class="mut">&mdash;</span>'
            open_rows.append(f"""
        <tr><td>{html.escape(LABEL[strat])}</td>
        <td class="num">{html.escape(sym.replace("USDT", ""))}</td>
        <td><span class="tag {pos['side']}">{pos['side']}</span></td>
        <td class="num">{pos['entry']:,.6g}</td><td>{now_px}</td>
        <td class="num">{pos['sl']:,.6g}</td><td class="num">{pos['tp']:,.6g}</td>
        <td>{unreal}</td></tr>""")
    open_html = ("".join(open_rows) if open_rows else
                 '<tr><td colspan="8" class="empty-row">No open positions &mdash; '
                 'both accounts are flat.</td></tr>')

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

    # --- recent trades ---
    recent = sorted(trades, key=lambda t: t["time"], reverse=True)[:20]
    trade_rows = "".join(f"""
        <tr><td class="num mut">{html.escape(t['time'][:16].replace('T', ' '))}</td>
        <td>{html.escape(LABEL[t['strategy']])}</td>
        <td class="num">{html.escape(t['symbol'].replace('USDT', ''))}</td>
        <td><span class="tag {t['side']}">{t['side']}</span></td>
        <td class="num">{t['price']:,.4f}</td>
        <td class="mut">{html.escape(t['reason'])}</td>
        <td class="num {cls_for(t['pnl'])}">{t['pnl']:+,.2f}</td></tr>"""
        for t in recent)
    if not trade_rows:
        trade_rows = ('<tr><td colspan="7" class="empty-row">No closed trades yet. '
                      'Each book trades only when all of its conditions line up on a '
                      'closed hourly candle.</td></tr>')

    legend = "".join(
        f'<span><span class="swatch" style="background:var(--{SERIES[s]})"></span>'
        f'{html.escape(LABEL[s])}</span>' for s in SHOWN)

    return f"""<title>Bob</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Serif:wght@600&display=swap">
<style>{CSS}</style>
<div class="wrap">

  <header>
    <div>
      <h1>Bob</h1>
      <p class="tagline">Bob runs two rule-based strategies across {len(pt.SYMBOLS)} crypto
        markets on live prices with simulated money, against a random-entry control
        and buy-and-hold. No exchange account, no real capital. The question is not
        which book is ahead — it is whether either beats noise.</p>
    </div>
    <div class="stamp">
      {pill}
      <span class="lbl">Updated {generated:%d %b %Y · %H:%M} UTC</span>
    </div>
  </header>

  <div class="verdict">
    <div class="verdict-head"><h2>{html.escape(headline)}</h2></div>
    <p>{html.escape(body)}</p>
    <div class="meter">
      <div class="meter-row">
        <span class="lbl">Evidence collected</span>
        <span class="num" style="font-size:13px">{total_trades} of ~{SIGNIFICANCE_TARGET * 2} trades</span>
      </div>
      <div class="meter-track"><div class="meter-fill" style="width:{pct_done:.1f}%"></div></div>
      <div class="meter-row">
        <span class="mut" style="font-size:12.5px">Roughly {SIGNIFICANCE_TARGET} trades
          per strategy before returns can be told apart from luck.</span>
      </div>
    </div>
  </div>

  <div class="grid2">{"".join(cards)}</div>

  <section>
    <div class="sec-head"><h2>Equity curve</h2><div class="legend">{legend}</div></div>
    <div class="sec-body"><div class="chart">{render_chart(curves)}</div></div>
  </section>

  <section>
    <div class="sec-head"><h2>Open positions</h2></div>
    <div class="scroll"><table>
      <thead><tr><th>Strategy</th><th>Market</th><th>Side</th><th>Entry</th>
        <th>Now</th><th>Stop</th><th>Target</th><th>Unrealized</th></tr></thead>
      <tbody>{open_html}</tbody></table></div>
  </section>

  <section>
    <div class="sec-head"><h2>What each strategy sees right now</h2></div>
    <div class="scroll"><table>
      <thead><tr><th>Market</th><th>Price</th><th>Trend</th><th>RSI</th><th>Stoch</th>
        <th>MFI</th><th>ADX</th>
        {"".join(f'<th>{html.escape(LABEL[s].split(chr(183))[0].strip())}</th>' for s in SHOWN)}
      </tr></thead>
      <tbody>{"".join(sig_rows)}</tbody></table></div>
  </section>

  <section>
    <div class="sec-head"><h2>Closed trades</h2>
      <span class="lbl">{total_trades} total</span></div>
    <div class="scroll"><table>
      <thead><tr><th>Closed</th><th>Strategy</th><th>Market</th><th>Side</th>
        <th>Exit</th><th>Reason</th><th>P&amp;L</th></tr></thead>
      <tbody>{trade_rows}</tbody></table></div>
  </section>

  <footer>Simulated trading only &mdash; no orders are placed and no real money is
    at risk. Each strategy runs one {fmt_money(pt.INITIAL_CAPITAL)} account across
    {len(pt.SYMBOLS)} markets on {pt.INTERVAL} candles, sizing every position at
    1/{pt.MAX_CONCURRENT} of equity with a {pt.ATR_MULT_SL}&times; ATR stop and
    {pt.RR}R target. The book halts for {pt.HALT_COOLDOWN_HOURS}h after
    {pt.MAX_CONSEC_LOSS} consecutive losses or a {pt.MAX_DD_PCT:.0f}% drawdown.</footer>
</div>
"""


def generate(out_file=OUT_FILE, live=None, fetch_live=True, bench=None):
    """Render the dashboard. Pass `live` to reuse indicators already computed
    by the trading loop instead of re-fetching every symbol."""
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
    page = build_html(state, trades, live, datetime.now(timezone.utc), bench=bench)
    with open(out_file, "w") as f:
        f.write(page)
    return out_file, len(trades), len(state)


if __name__ == "__main__":
    path, n_trades, n_books = generate()
    print(f"wrote {path} — {n_books} books, {n_trades} closed trades")
