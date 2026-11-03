from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import re
import time
from .config import UNIVERSE, DEFAULT_SYMBOLS
from .providers import Providers, ProviderError, now_ist
from .analysis import indicators, score_evidence, intraday_plan, analyse_options
from . import llm
from .patterns import chart_structure, oi_summary
from .learning import PatternLearner
from .sectors import metadata
from .reasoning import analyse, rank_key

def resolve_symbols(text):
    symbols = []
    # Remove compound index names before resolving NIFTY, to avoid BANKNIFTY becoming two indices.
    lowered = text.lower()
    if re.search(r'\b(banknifty|bank nifty|nifty bank)\b', lowered):
        symbols.append('BANKNIFTY')
        lowered = re.sub(r'\b(banknifty|bank nifty|nifty bank)\b', '', lowered)
    for symbol, meta in UNIVERSE.items():
        if any(re.search(r'(?<!\w)' + re.escape(term.lower()) + r'(?!\w)', lowered) for term in [symbol, meta['name'], *meta['aliases']]):
            if symbol not in symbols:
                symbols.append(symbol)
    # Explicit SYMBOL.NS is supported beyond the curated shortlist.
    for match in re.findall(r'\b([A-Z][A-Z0-9&-]{0,19})\.NS\b', text.upper()):
        if match not in symbols:
            symbols.append(match)
    return symbols[:6]

def requested_mode(text, selected):
    if re.search(r'\b(options?|calls?|puts?|expiry|greeks)\b', text.lower()):
        return 'options'
    if re.search(r'long.?term|invest|years?|parents|retirement', text.lower()):
        return 'longterm'
    if re.search(r'intraday|day trad|today.*trad', text.lower()):
        return 'intraday'
    return selected

class ResearchAgent:
    def __init__(self, settings, providers=None):
        self.settings = settings
        self.providers = providers or Providers(settings)
        self.learner = PatternLearner(settings.data_dir)

    def run(self, question, mode, watchlist, capital, risk_pct, demo, context, expiry='current_week', progress=None):
        started = time.monotonic()
        trace = []
        def step(agent, detail, status='done'):
            event = {'agent': agent, 'detail': detail, 'status': status, 'elapsed_ms': round((time.monotonic()-started)*1000)}
            trace.append(event)
            if progress:
                progress(event)
        mode = requested_mode(question, mode)
        explicit = resolve_symbols(question)
        previous = context[0].get('symbols', []) if context else []
        followup = bool(re.search(r'\b(it|these|those|them|why|risk|compare|more|which|same|this)\b', question.lower()))
        scope = explicit or (previous if followup and previous else []) or watchlist or (['NIFTY'] if mode == 'options' else DEFAULT_SYMBOLS)
        if mode == 'options' and not explicit and not (followup and previous):
            scope = ['NIFTY']
        planned = None
        if self.settings.ai_enabled and not demo:
            try:
                allowed = list(dict.fromkeys([*UNIVERSE, *scope]))
                planned = llm.plan(self.settings, question, context, allowed, mode)
                # Respect explicit symbols and follow-up scope; the model selects only an unspecified shortlist.
                if not explicit and not (followup and previous):
                    scope = planned['symbols']
                step('AI planner', planned['focus'] or 'Selected market research tools')
            except ProviderError as e:
                step('AI planner', f'{e}; using deterministic planner', 'fallback')
        else:
            step('Planner', 'Deterministic tool routing; AI planner not active in demo or without key/model')
        scope = list(dict.fromkeys(scope))[:6]
        step('Planner', f'{mode} research for {", ".join(scope)}')
        market = self.providers.market_status(demo)
        step('Market session', market['label'], 'done' if market['verified'] else 'limited')
        evidence = {s: {'symbol': s, 'news': {'articles': []}, 'gaps': []} for s in scope}
        tasks = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            if hasattr(self.providers, 'quotes'):
                tasks[pool.submit(self.providers.quotes, scope, demo)] = (None, 'quotes')
            for symbol in scope:
                tasks[pool.submit(self.providers.news, symbol, mode, demo)] = (symbol, 'news')
                tasks[pool.submit(self.providers.candles, symbol, mode, demo)] = (symbol, 'prices')
                if mode == 'longterm' and not demo:
                    tasks[pool.submit(self.providers.fundamentals, symbol)] = (symbol, 'fundamentals')
                if hasattr(self.providers,'options') and (mode == 'options' or (not demo and (self.settings.broker_enabled or re.search(r'\boi\b|open interest|put.?call',question,re.I)))):
                    tasks[pool.submit(self.providers.options, symbol, expiry, demo)] = (symbol, 'options')
            for task in as_completed(tasks):
                symbol, tool = tasks[task]
                try:
                    value = task.result()
                    if tool == 'quotes':
                        if value['market'].get('verified'):
                            market = value['market']
                        for q in value['quotes']:
                            evidence[q['symbol']]['quote'] = q
                        for s in scope:
                            evidence[s]['gaps'].extend('quotes: '+gap for gap in value.get('gaps',[]) if gap.startswith(s+':') or not any(gap.startswith(other+':') for other in scope))
                        step('Quote tool', f'Obtained {len(value["quotes"])} current price snapshots')
                        continue
                    evidence[symbol][tool] = value
                    step(tool.title()+' tool', f'{symbol}: evidence fetched')
                except (ProviderError, ValueError, KeyError, TypeError) as e:
                    error = str(e) if isinstance(e, ProviderError) else 'Provider returned malformed evidence'
                    if symbol is not None:
                        evidence[symbol]['gaps'].append(f'{tool}: {error}')
                    step(tool.title()+' tool', f'{symbol}: {error}', 'limited')
        recommendations = []
        for symbol in scope:
            e = evidence[symbol]
            meta = UNIVERSE.get(symbol, metadata(symbol))
            bundle = e.get('prices', {'candles': [], 'source': 'Unavailable', 'synthetic': demo, 'broker': False})
            metrics = indicators(bundle, mode) if bundle['candles'] else {'available': False, 'reason': 'Price data unavailable'}
            articles = e['news']['articles']
            fundamentals = e.get('fundamentals')
            scored = score_evidence(metrics, articles, fundamentals, mode)
            structure = chart_structure(bundle, mode)
            try:
                prediction = self.learner.inspect(symbol, bundle, mode)
            except (ValueError, OSError, ZeroDivisionError):
                prediction = {'status': 'UNAVAILABLE', 'reason': 'Pattern model could not train on available history'}
            step('Pattern learner', f'{symbol}: {prediction["status"]}', 'done' if prediction['status']=='EXPERIMENTAL MODEL' else 'limited')
            if mode == 'longterm' and metrics.get('available') and metrics['age_minutes'] > 7*1440:
                e['gaps'].append('Daily candles are over seven days old; refresh market history before reviewing this candidate.')
                scored['risks'].append('Daily market data is stale; the screening score is historical only.')
            rec = {'symbol': symbol, 'name': meta['name'], 'sector': meta['sector'], 'metrics': metrics,
                   'price_source': bundle['source'], 'price_as_of': bundle.get('as_of'),
                   'news': articles, 'news_fetched_at': e['news'].get('fetched_at'),
                   'fundamentals': fundamentals, 'score': scored['score'], 'components': scored['components'],
                   'scoring_method': scored['method'], 'reasons': scored['reasons'],
                   'risks': scored['risks'], 'gaps': e['gaps'] + bundle.get('provider_notes', []), 'plan': None,
                   'demo': demo, 'status': 'RESEARCH ONLY'}
            rec.update(quote=e.get('quote'), chart_structure=structure, prediction=prediction,
                       oi_analysis=oi_summary(e['options']) if 'options' in e else None)
            if structure.get('available'):
                rec['reasons'].append(f"Observed support {structure['levels']['support']} and resistance {structure['levels']['resistance']}; {len(structure['patterns'])} chart pattern(s).")
            if mode == 'intraday':
                rec['plan'] = intraday_plan(metrics, bundle, scored, market, capital, risk_pct, demo)
                if rec['plan']['status'] == 'CONDITIONAL SETUP' and (not rec['quote'] or not rec['quote'].get('live')):
                    rec['plan'] = {'status': 'WAIT', 'blocked_by': ['A fresh exchange-timestamped broker quote is also required; the chart close is not a live execution price.']}
                rec['status'] = rec['plan']['status']
            elif mode == 'options':
                rec['option_analysis'] = analyse_options(e['options'], market) if 'options' in e else {
                    'status': 'WAIT', 'contracts': [], 'limitations': ['Configure Zerodha Kite data access or Upstox and select a valid underlying/expiry to inspect the real chain.']}
                rec['status'] = rec['option_analysis']['status']
            elif fundamentals and fundamentals.get('complete',True) and metrics.get('available') and metrics['age_minutes'] <= 7*1440 and scored['score'] >= 65:
                rec['status'] = 'LONG-TERM REVIEW CANDIDATE'
            if demo:
                rec['status'] = 'DEMO RESEARCH'
            recommendations.append(rec)
        recommendations.sort(key=lambda r: r['score'], reverse=True)
        for rec in recommendations:
            rec['scenario'] = self.scenario(rec, mode)
        step('Evidence analyst', 'Calculated completed-candle indicators and transparent screening scores')
        step('Risk reviewer', 'Checked freshness, missing evidence, budget and setup restrictions')
        report = {'created_at': now_ist().isoformat(), 'question': question, 'mode': mode, 'symbols': scope,
                  'demo': demo, 'market': market, 'recommendations': recommendations, 'trace': trace,
                  'capital': capital, 'risk_pct': risk_pct, 'expiry': expiry, 'engine': 'Deterministic research',
                  'disclosure': 'Research hypotheses, not guaranteed returns. Screening scores are uncalibrated heuristics, not probabilities. No orders are executed.'}
        if hasattr(self.providers,'desk'):
            from .context import enrich
            enrich(report,self.providers,self.providers.desk)
            step('Market/event reviewer','Checked daily regime, aligned relative strength and sourced calendar flags')
        for rec in recommendations:
            daily=None
            if mode=='intraday' and not demo:
                try:daily=self.providers.candles(rec['symbol'],'longterm')
                except ProviderError:pass
            analyse(rec,mode,daily)
            deep=rec['deep_analysis'];conflicts=[]
            if deep['news']['conflicting']:conflicts.append('Positive and negative news catalysts conflict; wait for clarification.')
            patterns=rec.get('chart_structure',{}).get('patterns',[])
            if any(p['direction']=='bearish' for p in patterns) and not any(p['direction']=='bullish' for p in patterns):conflicts.append('Confirmed bearish chart structure conflicts with a long setup.')
            if mode=='intraday' and rec['status']=='CONDITIONAL SETUP' and conflicts:
                rec['status']='WAIT · CONFLICTING EVIDENCE';rec['plan']={'status':'WAIT','blocked_by':conflicts}
                rec['risks']=conflicts+rec['risks'];deep['counterarguments']=conflicts+deep['counterarguments'][:2]
            if mode=='longterm' and rec['status']=='LONG-TERM REVIEW CANDIDATE':
                f=rec.get('fundamentals') or {}
                if rec['metrics'].get('trend')!='Bullish':
                    rec['status']='RESEARCH ONLY'
                if f.get('operating_cash_flow',0)<=0 or f.get('earnings_growth_pct',0)<=0:rec['status']='RESEARCH ONLY'
            if mode=='intraday' and daily:
                from .analysis import indicators as daily_indicators
                if daily_indicators(daily,'longterm').get('trend')=='Bearish':
                    rec['status']='WAIT · DAILY TREND CONFLICT';rec['plan']={'status':'WAIT','blocked_by':['Daily trend conflicts with the intraday long setup.']}
        recommendations.sort(key=rank_key)
        step('Evidence cross-check','Compared trend, momentum, volume, patterns, news catalysts and opposing evidence; missing facts remain missing')
        report['answer'] = self.fallback_answer(report, question)
        if self.settings.ai_enabled and not demo:
            try:
                report['ai_commentary'] = llm.explain(self.settings, question, context, report, planned)
                report['engine'] = 'OpenAI tool planner + evidence pipeline' if planned else 'Evidence pipeline + OpenAI explanation'
                step('AI explainer', 'Added evidence-grounded commentary; structured result remains authoritative')
            except ProviderError as e:
                step('AI explainer', f'{e}; retained deterministic explanation', 'fallback')
        step('Research synthesis', 'Ranked shortlist and explanations ready')
        report['duration_ms'] = round((time.monotonic()-started)*1000)
        return report

    @staticmethod
    def scenario(rec, mode):
        m=rec['metrics']
        levels=rec.get('chart_structure',{}).get('levels')
        if not m.get('available') or not levels or m['atr14']<=0 or mode=='options':
            return None
        if mode=='intraday':
            entry=round(max(levels['range_high'],m['price'])*1.001,2)
            stop=round(entry-1.5*m['atr14'],2)
            target=round(entry+3*m['atr14'],2)
            trigger='Consider only after a confirmed volume-backed breakout above entry, with a fresh quote. Exit before session close; do not carry an intraday hypothesis overnight.'
        else:
            entry=round(levels['support']+.25*m['atr14'],2)
            stop=round(levels['support']-1.5*m['atr14'],2)
            target=levels['resistance']
            trigger='Potential pullback area for review; require business quality, valuation and a confirmed reversal. Daily-chart levels are not a long-term fair value estimate.'
        if stop<=0 or target<=entry:
            return None
        return {'status':'ILLUSTRATIVE LEVELS ONLY','entry':entry,'invalidation':stop,'target':target,
                'upside_to_target_pct':round((target/entry-1)*100,2),'risk_reward':round((target-entry)/(entry-stop),2),
                'as_of':m['as_of'],'trigger':trigger,
                'not_actionable_reason':'Synthetic example' if rec['demo'] else 'Setup/data checks have not passed' if rec['status']!='CONDITIONAL SETUP' else 'Revalidate current quote and conditions before acting.'}

    @staticmethod
    def fallback_answer(report, question):
        recs = report['recommendations']
        if not recs:
            return 'No symbols selected. Add a watchlist or use an NSE symbol such as INFY.NS.'
        prefix = 'Synthetic demo: these prices and headlines are examples. ' if report['demo'] else ''
        top = recs[0]
        if not any(r['metrics'].get('available') or r['news'] for r in recs):
            return prefix+'The research tools could not obtain enough evidence. I cannot rank a stock reliably. Check the provider gaps below and retry; do not treat this as a buy signal.'
        answer = prefix+f"{top['name']} leads this limited {report['mode']} shortlist with a screening score of {top['score']}. The score measures heuristic alignment, not expected profit. "
        if re.search(r'why|risk|downside|avoid', question.lower()):
            answer += 'Key counterarguments: '+' '.join(top['risks'][:3])+' '
        else:
            answer += ' '.join(top['reasons'][:2])+' '
        if report['mode'] == 'intraday':
            ready = [r for r in recs if r['status'] == 'CONDITIONAL SETUP']
            answer += f'{len(ready)} conditional setup(s) passed the data and signal checks. ' if ready else 'Wait: no stock passed all fresh-data and setup checks. '
        elif report['mode'] == 'options':
            answer += 'Options are chain research only. A directional headline cannot identify a suitable strike, expiry or maximum loss.'
        else:
            answer += 'Review financial statements, valuation and business risks before a long-term purchase. Missing financials keep this at research-only status.'
        levels=top.get('chart_structure',{}).get('levels')
        if levels:
            answer += f" Observed support is ₹{levels['support']} and resistance is ₹{levels['resistance']}."
        scenario=top.get('scenario')
        if scenario:
            answer += f" Illustrative entry ₹{scenario['entry']}, target ₹{scenario['target']}, invalidation ₹{scenario['invalidation']}. {scenario['trigger']}"
        prediction=top.get('prediction',{})
        if prediction.get('status')=='EXPERIMENTAL MODEL':
            answer += f" The chart model achieved {prediction['validation']['direction_accuracy']*100:.1f}% on its chronological holdout; its forecast remains experimental."
        oi=top.get('oi_analysis')
        if oi and oi['expiries']:
            first=oi['expiries'][0]
            answer += f" For {first['expiry']}, the returned strikes have put/call OI ratio {first['pcr_oi']}; OI alone cannot prove market direction."
        return answer
