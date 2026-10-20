def ema(s,n): return s.ewm(span=n,adjust=False).mean()
