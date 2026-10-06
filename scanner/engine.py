import numpy as np
from config import settings
from scanner.indicators import ema,atr
from scanner.vcp import detect_vcp

def analyze(symbol,df,nifty_df=None):
    if df.empty or len(df)<260:return None
    x=df.copy(); last=x.iloc[-1]; prev=x.iloc[-2]
    x['ema21']=ema(x['close'],21); x['atr14']=atr(x,14)
    price=float(last.close); atrpct=float(x['atr14'].iloc[-1]/price*100)
    h52=float(x.high.iloc[-252:].max()); l52=float(x.low.iloc[-252:].min())
    fromhigh=(h52-price)/h52*100; swing=(price/l52-1)*100
    avg=x.volume.iloc[-21:-1].mean(); rvol=float(last.volume/avg) if avg>0 else 0
    red=x.iloc[-11:-1]; redmax=red.loc[red.close<red.open,'volume'].max()
    pocket=bool(last.close>last.open and last.volume>(redmax if np.isfinite(redmax) else 0))
    above=price>x.ema21.iloc[-1]; momentum=price>prev.close
    level=max(float(prev.high),float(prev.close))*(1+settings.BREAKOUT_BUFFER_PCT/100)
    normal=price>=level
    rs20=np.nan
    if nifty_df is not None and len(nifty_df)>=25:
        rs20=((price/x.close.iloc[-21]-1)-(nifty_df.close.iloc[-1]/nifty_df.close.iloc[-21]-1))*100
    vcp=detect_vcp(x)
    liquidity=price*last.volume>=settings.MIN_TURNOVER_CR*1e7
    parts={'trend':above,'near_high':fromhigh<=settings.CANDIDATE_NEAR_HIGH_PCT,
           'vcp':vcp['true_vcp'],'swing':swing>=settings.CANDIDATE_MIN_SWING,'liquidity':liquidity,'momentum':momentum}
    score=sum(bool(v) for v in parts.values())
    buy=all([price>=settings.MIN_PRICE,above,momentum,rvol>=settings.MIN_RVOL or pocket,
             normal or vcp['breakout'],atrpct>=settings.MIN_ATR_PCT,swing>=settings.MIN_SWING_RETURN,
             fromhigh<=settings.MAX_FROM_52W_HIGH,liquidity])
    failed=[]
    tests=[
      ('price',price>=settings.MIN_PRICE),('ema21',above),('momentum',momentum),
      ('rvol_or_pocket',rvol>=settings.MIN_RVOL or pocket),('breakout_or_vcp',normal or vcp['breakout']),
      ('atr',atrpct>=settings.MIN_ATR_PCT),('swing',swing>=settings.MIN_SWING_RETURN),
      ('near_52w_high',fromhigh<=settings.MAX_FROM_52W_HIGH),
      ('turnover',liquidity)]
    failed=[name for name,ok in tests if not ok]
    return {'symbol':symbol,'date':str(last.timestamp),'price':price,'candidate':score>=settings.CANDIDATE_SCORE_REQUIRED,
            'score':score,'buy':bool(buy),'vcp':vcp['true_vcp'],'vcp_breakout':vcp['breakout'],'vcp_quality':vcp['quality_score'],
            'vcp_failed':';'.join(vcp['reasons']),'pivot':vcp['pivot'],'rvol':rvol,'pocket_pivot':pocket,
            'atr_pct':atrpct,'rs20':rs20,'swing_return':swing,'from_52w_high':fromhigh,
            'failed_filters':';'.join(failed)}
