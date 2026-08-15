"""Cross-asset ETF bars. feed=sip and adjustment=all are mandatory (D6, D7)."""
import os, pandas as pd
from dotenv import load_dotenv; load_dotenv()
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed, Adjustment
from datetime import datetime

TICKERS = ['SPY','QQQ','IWM','EFA','EEM','TLT','IEF','LQD','HYG','GLD','SLV','DBC','VNQ','XLE','UUP']
c = StockHistoricalDataClient(os.environ['APCA_API_KEY_ID'], os.environ['APCA_API_SECRET_KEY'])
req = StockBarsRequest(symbol_or_symbols=TICKERS, timeframe=TimeFrame.Day,
                       start=datetime(2016,1,1), end=datetime(2026,8,13),
                       feed=DataFeed.SIP, adjustment=Adjustment.ALL)
df = c.get_stock_bars(req).df.reset_index()
df['date'] = pd.to_datetime(df['timestamp']).dt.tz_convert('America/New_York').dt.normalize().dt.tz_localize(None)
out = df[['symbol','date','open','high','low','close','volume']]
out.to_parquet('data/etfs.parquet')
g = out.groupby('symbol')['date'].agg(['min','max','count'])
print(g.to_string())
