"""Read-only Upstox statements and corporate events; no trading API calls."""
import calendar
from datetime import datetime
import hashlib
import re
from urllib.parse import quote
from .providers import finite, now_ist
from .net import CACHE, ProviderError


def numeric(value):
    if finite(value):return value
    if isinstance(value,str):
        try:
            parsed=float(value.replace(',','').replace('%','').strip())
            return parsed if finite(parsed) else None
        except ValueError:pass
    return None


def period_date(value):
    for fmt in ('%b %Y','%B %Y','%d %b %Y','%Y-%m-%d'):
        try:
            parsed=datetime.strptime(value,fmt).date()
            if fmt in ('%b %Y','%B %Y'):parsed=parsed.replace(day=calendar.monthrange(parsed.year,parsed.month)[1])
            return parsed
        except (ValueError,TypeError):continue
    return None


def latest(history):
    valid=[h for h in history if isinstance(h,dict) and period_date(h.get('period')) and period_date(h['period'])<=now_ist().date() and numeric(h.get('value')) is not None]
    return sorted(valid,key=lambda h:period_date(h['period']),reverse=True)


def growth(history):
    values=latest(history)
    if len(values)<2 or numeric(values[1]['value'])<=0:return None
    if not 300<=(period_date(values[0]['period'])-period_date(values[1]['period'])).days<=430:return None
    return round((numeric(values[0]['value'])/numeric(values[1]['value'])-1)*100,2)


def financial_review(isin, payloads, symbol):
    profile=payloads.get('profile') or {};income=payloads.get('income-statement') or {};cash=payloads.get('cash-flow') or {};balance=payloads.get('balance-sheet') or {}
    categories={r['category']:r.get('history',[]) for r in income.get('income_statement',[]) if isinstance(r,dict) and 'category' in r}
    flows={r['category']:r.get('history',[]) for r in cash.get('cash_flow',[]) if isinstance(r,dict) and 'category' in r}
    ratios={r['name']:r for r in payloads.get('key-ratios',[]) if isinstance(r,dict) and 'name' in r}
    revenue=latest(categories.get('revenue',[]));earnings=latest(categories.get('net_profit',[]));operating=latest(flows.get('operating',[]))
    # Total liabilities are not interest-bearing debt. Do not fabricate debt/equity.
    lines={r['particular'].lower():latest(r.get('history',[])) for r in balance.get('full_statement',[]) if isinstance(r,dict) and isinstance(r.get('particular'),str)}
    debt=lines.get('total borrowings') or lines.get('borrowings') or lines.get('total debt') or []
    equity=lines.get('total equity') or lines.get('shareholders equity') or []
    debt_equity=None
    if debt and equity and debt[0]['period']==equity[0]['period'] and numeric(equity[0]['value'])>0:
        debt_equity=round(numeric(debt[0]['value'])/numeric(equity[0]['value']),3)
    pe=numeric(ratios.get('P/E',{}).get('company_value'));roe=numeric(ratios.get('ROE',{}).get('company_value'))
    sector_pe=numeric(ratios.get('P/E',{}).get('sector_value'))
    periods={k:(v[0]['period'] if v else None) for k,v in [('revenue',revenue),('earnings',earnings),('cash_flow',operating),('debt',debt),('equity',equity)]}
    dates=[period_date(v) for v in periods.values() if v]
    warnings=[]
    if debt_equity is None:warnings.append('Interest-bearing debt/equity unavailable; total liabilities are not substituted.')
    if len(set(v for v in periods.values() if v))>1:warnings.append('Financial metrics cover different reporting periods.')
    age=(now_ist().date()-min(dates)).days if dates else None
    if age is None or age>450:warnings.append('Financial statement history is missing or over 450 days old.')
    ocf=numeric(operating[0]['value']) if operating else None
    if ocf is not None and cash.get('units_in')=='crore':ocf*=10_000_000
    elif ocf is not None:ocf=None;warnings.append('Cash-flow currency units could not be verified.')
    result={'symbol':symbol,'isin':isin,'sector':profile.get('sector'),'revenue_growth_pct':growth(categories.get('revenue',[])),
        'earnings_growth_pct':growth(categories.get('net_profit',[])),'roe_pct':roe,'debt_to_equity':debt_equity,
        'pe':pe,'sector_pe':sector_pe,'operating_cash_flow':ocf,'periods':periods,
        'as_of':str(min(dates)) if dates else None,'fetched_at':now_ist().isoformat(),'age_days':age,
        'source':'Upstox sourced financial statements and current ratios; verify company filings',
        'source_url':'https://upstox.com/developer/api-documentation/fundamentals/',
        'source_urls':['https://api.upstox.com/v2/fundamentals/'+isin+'/'+k for k in payloads],
        'warnings':warnings,'origin':'upstox','review_status':'SOURCE-LINKED · NOT AUDITED BY APP'}
    fields=('revenue_growth_pct','earnings_growth_pct','roe_pct','debt_to_equity','pe','operating_cash_flow')
    result['complete']=all(finite(result.get(k)) for k in fields) and pe>0 and debt_equity>=0 and not warnings
    result['valuation']='Above sector P/E' if pe and sector_pe and pe>sector_pe else 'At/below sector P/E' if pe and sector_pe else 'Sector comparison unavailable'
    return result


class UpstoxResearch:
    def __init__(self,providers):self.providers=providers

    def isin(self,symbol):
        key=self.providers.symbol_key(symbol)
        isin=key.split('|')[-1] if key else ''
        if not re.fullmatch(r'IN[A-Z0-9]{10}',isin):raise ProviderError('An equity ISIN is required for financials and corporate actions')
        return isin

    def review(self,symbol):
        isin=self.isin(symbol)
        def load():
            from concurrent.futures import ThreadPoolExecutor, as_completed
            parts={};warnings=[]
            routes={'profile':'profile','income-statement':'income-statement?type=consolidated&time_period=yearly',
                    'cash-flow':'cash-flow?type=consolidated','key-ratios':'key-ratios','balance-sheet':'balance-sheet?type=consolidated&fs=true'}
            with ThreadPoolExecutor(max_workers=3) as pool:
                tasks={pool.submit(self.providers._broker,'v2/fundamentals/'+isin+'/'+route):name for name,route in routes.items()}
                for task in as_completed(tasks):
                    name=tasks[task]
                    try:parts[name]=task.result()
                    except ProviderError as e:warnings.append(name+': '+str(e))
            if not parts:raise ProviderError('Upstox financial endpoints unavailable; check Analytics Token access')
            try:result=financial_review(isin,parts,symbol)
            except (ValueError,KeyError,TypeError,AttributeError):raise ProviderError('Malformed financial statement response') from None
            result['warnings'].extend(warnings)
            if warnings:result['complete']=False
            return result
        return CACHE.get_or_load(('financial-review',symbol,isin),3600,load)

    def events(self,symbol):
        isin=self.isin(symbol)
        def load():
            data=self.providers._broker('v2/fundamentals/'+isin+'/corporate-actions')
            if not isinstance(data,list):raise ProviderError('Malformed corporate actions')
            events=[]
            for row in data:
                day=period_date(row.get('expiry_date'));name=row.get('name','Corporate action')
                if not day:continue
                identity=hashlib.sha256((symbol+str(day)+name+str(row.get('ratio'))+str(row.get('amount'))).encode()).hexdigest()
                events.append({'id':'upstox:'+identity,'symbol':symbol,'kind':'corporate','date':str(day),
                    'title':name+((' · '+str(row['amount'])) if row.get('amount') is not None else '')+((' · '+str(row['ratio'])) if row.get('ratio') else ''),
                    'source_url':'https://api.upstox.com/v2/fundamentals/'+isin+'/corporate-actions',
                    'source':'Upstox corporate actions','reviewed':False,'fetched_at':now_ist().isoformat()})
            return events
        return CACHE.get_or_load(('corporate-events',symbol,isin),3600,load)
