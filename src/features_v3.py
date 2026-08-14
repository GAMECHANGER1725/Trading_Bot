"""
v3 features — information the model has never seen.

The v1/v2 feature set was fourteen transforms of one thing: a stock's own price
history. Measured standalone, thirteen of the fourteen were indistinguishable
from noise. That is not a modelling failure, it is an information failure, and
no amount of tuning fixes it.

These features are different in kind:

  * **Sector-relative momentum.** Ranking a semiconductor stock against all 500
    names mixes "is this stock strong?" with "is tech in favour?". The momentum
    literature finds the effect is substantially stronger *within* industry.
    Sectors are derived from return-correlation clusters rather than a
    classification file, so no external data and no point-in-time problem.
  * **Market regime.** Momentum famously inverts after market bottoms — the 2009
    momentum crash is the canonical example. A model that cannot see whether the
    market is calm, falling or recovering is being asked a question with the
    context removed.
  * **Breadth.** How many stocks are participating. Narrow rallies behave
    differently from broad ones.
  * **Dividend proximity.** Days to and from ex-dividend, from Alpaca's
    corporate actions endpoint. Free, live, and genuinely exogenous to price.

Everything here is computable in real time from sources the live bot has.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

V3_FEATURES = [
    "sect_rel_mom20",    # 20-day momentum minus the stock's sector's momentum
    "sect_rel_mom60",
    "sect_mom20",        # the sector's own momentum
    "mkt_vol20",         # market-wide realised volatility
    "mkt_mom20",         # market momentum
    "mkt_drawdown",      # how far the market is below its 1-year high
    "breadth_50",        # fraction of the universe above its own 50-day average
    "beta_60",           # the stock's sensitivity to the market
    "idio_vol60",        # volatility not explained by the market
    "days_to_div",       # sessions until next ex-dividend (capped)
    "days_since_div",
]


def assign_sectors(bars: pd.DataFrame, n_clusters: int = 11,
                   fit_end: str = "2019-12-31") -> pd.Series:
    """
    Cluster symbols by return correlation.

    Fitted only on data up to `fit_end` so cluster membership is not informed by
    the period being predicted. Eleven clusters mirrors the number of GICS
    sectors, without needing the GICS file.
    """
    from sklearn.cluster import KMeans

    hist = bars[bars["date"] <= fit_end]
    wide = hist.pivot_table(index="date", columns="symbol", values="close",
                            observed=True).pct_change()
    wide = wide.dropna(axis=1, thresh=int(len(wide) * 0.8)).fillna(0.0)
    if wide.shape[1] < n_clusters:
        return pd.Series("all", index=bars["symbol"].unique())

    corr = wide.corr().fillna(0.0)
    km = KMeans(n_clusters=n_clusters, n_init=10, random_state=42).fit(corr.values)
    sectors = pd.Series(km.labels_, index=corr.index).astype(str)
    # symbols that appear only after the fit window fall into a residual bucket
    missing = set(bars["symbol"].unique()) - set(sectors.index)
    for m in missing:
        sectors.loc[m] = "unassigned"
    return sectors


def build_v3(bars: pd.DataFrame, dividends: pd.DataFrame | None = None,
             sectors: pd.Series | None = None) -> pd.DataFrame:
    df = bars.sort_values(["symbol", "date"]).reset_index(drop=True).copy()
    df["date"] = pd.to_datetime(df["date"])
    g = df.groupby("symbol", observed=True)
    df["_ret"] = g["close"].pct_change()

    if sectors is None:
        sectors = assign_sectors(df)
    df["sector"] = df["symbol"].map(sectors).fillna("unassigned")

    # ---- stock momentum (raw, before ranking) ----
    df["_mom20"] = g["close"].pct_change(20)
    df["_mom60"] = g["close"].pct_change(60)

    # ---- sector aggregates: equal-weight mean across the sector, that day ----
    sect = df.groupby(["date", "sector"], observed=True)
    df["sect_mom20"] = sect["_mom20"].transform("mean")
    sect_mom60 = sect["_mom60"].transform("mean")
    df["sect_rel_mom20"] = df["_mom20"] - df["sect_mom20"]
    df["sect_rel_mom60"] = df["_mom60"] - sect_mom60

    # ---- market aggregates ----
    day = df.groupby("date", observed=True)
    mkt_ret = day["_ret"].transform("mean")
    df["_mkt_ret"] = mkt_ret
    mkt = df.drop_duplicates("date").set_index("date")["_mkt_ret"].sort_index()
    mkt_idx = (1 + mkt.fillna(0)).cumprod()
    mkt_tbl = pd.DataFrame({
        "mkt_vol20": mkt.rolling(20, min_periods=20).std(),
        "mkt_mom20": mkt_idx.pct_change(20),
        "mkt_drawdown": mkt_idx / mkt_idx.rolling(252, min_periods=60).max() - 1,
    })
    df = df.merge(mkt_tbl, left_on="date", right_index=True, how="left")

    # ---- breadth ----
    sma50 = g["close"].transform(lambda s: s.rolling(50, min_periods=50).mean())
    above = (df["close"] > sma50).astype(float)
    df["breadth_50"] = above.groupby(df["date"]).transform("mean")

    # ---- beta and idiosyncratic volatility ----
    cov = df.groupby("symbol", observed=True).apply(
        lambda t: t["_ret"].rolling(60, min_periods=60).cov(t["_mkt_ret"]),
        include_groups=False).reset_index(level=0, drop=True)
    mkt_var = df["_mkt_ret"].groupby(df["symbol"], observed=True).transform(
        lambda s: s.rolling(60, min_periods=60).var())
    df["beta_60"] = (cov / mkt_var.replace(0, np.nan)).clip(-5, 5)
    resid = df["_ret"] - df["beta_60"] * df["_mkt_ret"]
    df["idio_vol60"] = resid.groupby(df["symbol"], observed=True).transform(
        lambda s: s.rolling(60, min_periods=60).std())

    # ---- dividend proximity ----
    df["days_to_div"] = 60.0
    df["days_since_div"] = 60.0
    if dividends is not None and len(dividends):
        dv = dividends.copy()
        dv["ex_date"] = pd.to_datetime(dv["ex_date"])
        parts = []
        for sym, grp in df.groupby("symbol", observed=True):
            ex = dv.loc[dv["symbol"] == sym, "ex_date"].sort_values().to_numpy()
            d = grp["date"].to_numpy()
            if len(ex) == 0:
                parts.append(pd.DataFrame({"days_to_div": 60.0, "days_since_div": 60.0},
                                          index=grp.index))
                continue
            nxt = np.searchsorted(ex, d, side="left")
            to = np.where(nxt < len(ex),
                          (ex[np.clip(nxt, 0, len(ex) - 1)] - d) / np.timedelta64(1, "D"), 60.0)
            prv = nxt - 1
            since = np.where(prv >= 0,
                             (d - ex[np.clip(prv, 0, len(ex) - 1)]) / np.timedelta64(1, "D"), 60.0)
            parts.append(pd.DataFrame({"days_to_div": np.clip(to, 0, 60),
                                       "days_since_div": np.clip(since, 0, 60)},
                                      index=grp.index))
        prox = pd.concat(parts).sort_index()
        df["days_to_div"] = prox["days_to_div"]
        df["days_since_div"] = prox["days_since_div"]

    # ---- rank cross-sectionally, same convention as v1 ----
    if "is_member" in df.columns:
        df = df[df["is_member"]].copy()
    byday = df.groupby("date", observed=True)
    for f in V3_FEATURES:
        if df[f].nunique() > 1:
            df[f] = byday[f].rank(pct=True)

    return df[["symbol", "date", "sector"] + V3_FEATURES].dropna().reset_index(drop=True)
