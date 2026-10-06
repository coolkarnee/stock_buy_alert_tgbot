import pandas as pd

def ema(s,n): return s.ewm(span=n,adjust=False).mean()

def atr(df,n=14):
    p=df['close'].shift(1)
    tr=pd.concat([df['high']-df['low'],(df['high']-p).abs(),(df['low']-p).abs()],axis=1).max(axis=1)
    return tr.rolling(n).mean()
