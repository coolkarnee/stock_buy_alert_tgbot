import pandas as pd
from scanner.upstox import load_nse_equities,find_nifty500_key,historical_daily,historical_many
from scanner.engine import analyze
from scanner.alerts import send,format_result

def run(limit=None):
    universe=load_nse_equities()
    if limit:
        universe=universe.head(limit)
    print(f'Scanning {len(universe)} NSE equities...')

    nifty_key=find_nifty500_key()
    nifty_df=historical_daily(nifty_key,years=2)
    print(f'Nifty 500 benchmark: {nifty_key}, {len(nifty_df)} daily bars')

    results=[]
    status_rows=[]
    data=historical_many(universe,years=2,max_workers=6)

    for _,row in universe.iterrows():
        symbol=row.trading_symbol
        _,df,error=data.get(row.instrument_key,(symbol,pd.DataFrame(),'missing'))

        if error:
            status_rows.append({'symbol':symbol,'instrument_key':row.instrument_key,'data_status':'download_failed','error':error})
            print(f'[{symbol}] download failed: {error}')
            continue

        if df.empty:
            status_rows.append({'symbol':symbol,'instrument_key':row.instrument_key,'data_status':'no_data','error':'No historical candles returned'})
            print(f'[{symbol}] no historical data')
            continue

        if len(df)<260:
            status_rows.append({'symbol':symbol,'instrument_key':row.instrument_key,'data_status':'insufficient_history','history_bars':len(df),'error':f'Only {len(df)} daily bars returned'})
            print(f'[{symbol}] insufficient history: {len(df)} bars')
            continue

        try:
            result=analyze(symbol,df,nifty_df)
            if result:
                result['data_status']='ok'
                result['history_bars']=len(df)
                result['error']=''
                results.append(result)
                # Telegram alerts are BUY-only. Candidate results remain in scan_results.csv
                # for analysis but are not sent as Telegram stock alerts.
                if result['buy']:
                    send(format_result(result,'BUY'))
        except Exception as exc:
            status_rows.append({'symbol':symbol,'instrument_key':row.instrument_key,'data_status':'analysis_failed','history_bars':len(df),'error':str(exc)})
            print(f'[{symbol}] analysis failed: {exc}')

    out=pd.DataFrame(results)
    failures=pd.DataFrame(status_rows)

    if not out.empty:
        out=out.sort_values(['buy','candidate','vcp_quality','score'],ascending=False)

    if not failures.empty and not out.empty:
        for col in out.columns:
            if col not in failures.columns:
                failures[col]=pd.NA
        failures=failures[out.columns]

    combined=pd.concat([out,failures],ignore_index=True,sort=False)

    if not combined.empty:
        combined.to_csv('scan_results.csv',index=False)

    print('\n=== Scan health ===')
    print(f'Universe: {len(universe)}')
    print(f'Download/analyze OK: {len(out)}')
    print(f'Failed/skipped: {len(failures)}')
    print(f'Status: {combined["data_status"].value_counts().to_dict() if "data_status" in combined.columns else {}}')

    if not out.empty:
        print('\nTop candidates:')
        print(out[out.candidate].head(30).to_string(index=False))
        print(f'\nBUY={int(out.buy.sum())} CANDIDATE={int(out.candidate.sum())} VCP={int(out.vcp.sum())} VCP_BREAKOUT={int(out.vcp_breakout.sum())}')
    else:
        print('\nNo successfully analyzed stocks.')

    return combined

if __name__=='__main__':
    run()
