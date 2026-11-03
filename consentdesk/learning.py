"""Experimental price-pattern learner with chronological validation.

The LLM explains results; it does not create the forecast. News and OI are not
trained without a timestamped historical dataset. No accuracy claim is assumed.
"""
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import statistics
import uuid
from .providers import IST, now_ist
from .net import CACHE

FEATURES = ['return_1', 'return_5', 'sma20_distance', 'sma50_distance', 'rsi14', 'range_position', 'relative_volume', 'atr_pct', 'body_range', 'lower_wick_range']
VERSION = 1

def completed_rows(rows, mode, now=None):
    now = now or now_ist()
    if mode == 'longterm':
        return [r for r in rows if datetime.fromtimestamp(r['t'], IST).date() < now.date() or
                (datetime.fromtimestamp(r['t'], IST).date() == now.date() and (now.hour, now.minute) >= (15,35))]
    return [r for r in rows if r['t']+300 <= now.timestamp()]

def feature_at(rows, i):
    if i < 50:
        return None
    recent = rows[i-49:i+1]
    price = rows[i]['c']
    close20 = sum(r['c'] for r in recent[-20:])/20
    close50 = sum(r['c'] for r in recent)/50
    changes = [rows[j]['c']-rows[j-1]['c'] for j in range(i-13, i+1)]
    gains, losses = sum(max(v,0) for v in changes), sum(max(-v,0) for v in changes)
    rsi = .5 if gains+losses == 0 else gains/(gains+losses)
    high, low = max(r['h'] for r in recent[-20:]), min(r['l'] for r in recent[-20:])
    average_volume = sum(r['v'] for r in recent[-21:-1])/20
    tr = [max(rows[j]['h']-rows[j]['l'],abs(rows[j]['h']-rows[j-1]['c']),abs(rows[j]['l']-rows[j-1]['c'])) for j in range(i-13,i+1)]
    current = rows[i]
    width = current['h']-current['l']
    return [price/rows[i-1]['c']-1, price/rows[i-5]['c']-1, price/close20-1, price/close50-1,
            rsi, (price-low)/(high-low) if high>low else .5,
            min(10, current['v']/average_volume) if average_volume else 0,
            sum(tr)/14/price, (price-current['o'])/width if width else 0,
            (min(price,current['o'])-current['l'])/width if width else 0]

def dataset(rows, mode, cost_bps=20):
    horizon = 20 if mode == 'longterm' else 6
    result = []
    for i in range(50, len(rows)-horizon):
        target = i+horizon
        if mode != 'longterm' and datetime.fromtimestamp(rows[i]['t'],IST).date() != datetime.fromtimestamp(rows[target]['t'],IST).date():
            continue
        # Decision uses bar i's completed features; simulated entry is next bar open.
        ret = rows[target]['c']/rows[i+1]['o']-1-cost_bps/10000
        result.append({'x':feature_at(rows,i),'y':int(ret>0),'return':ret,'t':rows[i]['t'],'target_t':rows[target]['t'], 'index':i})
    return result

def sigmoid(z):
    return 1/(1+math.exp(-max(-35,min(35,z))))

def fit(train):
    means = [statistics.mean(row['x'][j] for row in train) for j in range(len(FEATURES))]
    scales = [max(1e-8,statistics.pstdev(row['x'][j] for row in train)) for j in range(len(FEATURES))]
    xs = [[max(-8,min(8,(v-means[j])/scales[j])) for j,v in enumerate(row['x'])] for row in train]
    weights = [0.]*len(FEATURES)
    prior = max(.01,min(.99,statistics.mean(row['y'] for row in train)))
    intercept = math.log(prior/(1-prior))
    for _ in range(180):
        gradient = [0.]*len(weights)
        bias = 0.
        for row,x in zip(train,xs):
            error = sigmoid(intercept+sum(w*v for w,v in zip(weights,x)))-row['y']
            bias += error
            for j,v in enumerate(x):
                gradient[j] += error*v
        intercept -= .06*bias/len(train)
        weights = [w-.06*(g/len(train)+.02*w) for w,g in zip(weights,gradient)]
    return {'weights':weights,'intercept':intercept,'means':means,'scales':scales,'prior':prior}

def probability(model,x):
    scaled = [max(-8,min(8,(v-model['means'][j])/model['scales'][j])) for j,v in enumerate(x)]
    return sigmoid(model['intercept']+sum(w*v for w,v in zip(model['weights'],scaled)))

def wilson(wins, count):
    if not count:
        return [0.,1.]
    z=1.96;p=wins/count;denom=1+z*z/count
    center=(p+z*z/(2*count))/denom
    margin=z*math.sqrt(p*(1-p)/count+z*z/(4*count*count))/denom
    return [round(center-margin,4),round(center+margin,4)]

def evaluate(model, test, horizon):
    predictions = [probability(model,r['x']) for r in test]
    accuracy = statistics.mean(int(p>=.5)==r['y'] for p,r in zip(predictions,test))
    brier = statistics.mean((p-r['y'])**2 for p,r in zip(predictions,test))
    baseline_accuracy = statistics.mean(int(model['prior']>=.5)==r['y'] for r in test)
    baseline_brier = statistics.mean((model['prior']-r['y'])**2 for r in test)
    selected = []
    next_index = -1
    for p,r in zip(predictions,test):
        if p>=.65 and r['index']>=next_index:
            selected.append(r['return']);next_index=r['index']+horizon+1
    wins=sum(r>0 for r in selected)
    equity=1.;peak=1.;drawdown=0.
    for r in selected:
        equity *= 1+r;peak=max(peak,equity);drawdown=max(drawdown,1-equity/peak)
    profit=sum(max(r,0) for r in selected);loss=-sum(min(r,0) for r in selected)
    return {'holdout_samples':len(test),'direction_accuracy':round(accuracy,4),'baseline_accuracy':round(baseline_accuracy,4),
        'brier_score':round(brier,4),'baseline_brier':round(baseline_brier,4),'simulated_trades':len(selected),
        'net_win_rate':round(wins/len(selected),4) if selected else None,'win_rate_95pct_interval':wilson(wins,len(selected)),
        'mean_net_return_pct':round(statistics.mean(selected)*100,3) if selected else None,
        'profit_factor':round(profit/loss,3) if loss>0 else None,'max_drawdown_pct':round(drawdown*100,3),
        'test_from':datetime.fromtimestamp(test[0]['t'],IST).isoformat(),'test_to':datetime.fromtimestamp(test[-1]['target_t'],IST).isoformat(),
        'notes':'Chronological holdout. Forecast labels can overlap; simulated trades do not overlap. No realistic stop/target fill or news/OI backtest. Cost assumption may understate actual fees/slippage.'}

class PatternLearner:
    def __init__(self,data_dir):
        self.directory=Path(data_dir)/'models'

    def inspect(self,symbol,bundle,mode,now=None):
        mode='longterm' if mode=='longterm' else 'intraday'
        if bundle.get('synthetic'):
            return {'status':'NOT TRAINED','reason':'Synthetic data is never used to claim model skill.'}
        rows=completed_rows(bundle.get('candles',[]),mode,now)
        samples=dataset(rows,mode)
        if len(samples)<180:
            return {'status':'INSUFFICIENT HISTORY','reason':'Need at least 180 labelled historical samples after feature lookback and horizon filtering.', 'samples':len(samples)}
        digest=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()
        key=('pattern-model',symbol,mode,digest)
        def train():
            path=self.directory/f'{symbol}-{mode}.json'
            try:
                stored=json.loads(path.read_text(encoding='utf-8'))
                if stored.get('dataset_hash')==digest and stored.get('version')==VERSION:
                    return stored
            except (ValueError,OSError):
                pass
            split=int(len(samples)*.7)
            test=samples[split:]
            train=[r for r in samples[:split] if r['target_t']<test[0]['t']]
            if len(train)<100 or len({r['y'] for r in train})<2:
                return {'status':'INSUFFICIENT DIVERSITY','reason':'Need 100 training observations and both outcome classes.'}
            model=fit(train)
            metrics=evaluate(model,test,20 if mode=='longterm' else 6)
            # Prediction uses the model trained before the holdout; no test-set refit.
            p=probability(model,feature_at(rows,len(rows)-1))
            stored={'version':VERSION,'dataset_hash':digest,'status':'EXPERIMENTAL MODEL','symbol':symbol,'mode':mode,'trained_at':now_ist().isoformat(),
                'feature_as_of':datetime.fromtimestamp(rows[-1]['t'],IST).isoformat(),'horizon':'20 trading bars' if mode=='longterm' else '6 five-minute bars in the same session',
                'cost_bps':20,'train_samples':len(train),'features':FEATURES,'model':model,'validation':metrics,
                'estimated_positive_net_return_probability':round(p,4),'validated_for_live_trading':False,
                'reason':'Experimental, uncalibrated forecast. No walk-forward or paper-trading certification; do not interpret probability as achieved accuracy. Raw provider history needs corporate-action validation; news and OI are not training features.'}
            self.directory.mkdir(parents=True,exist_ok=True)
            tmp=path.with_suffix('.'+uuid.uuid4().hex+'.tmp');tmp.write_text(json.dumps(stored,allow_nan=False),encoding='utf-8');tmp.replace(path)
            return stored
        result=CACHE.get_or_load(key,300,train)
        return {k:v for k,v in result.items() if k not in ('model','dataset_hash')}


def walk_forward(bundle,mode,cost_bps=20,now=None):
    """Three fixed expanding-window tests; train labels end before each test.

    Test tails are purged at fold boundaries so simulated positions cannot
    overlap across models. This evaluates only the chart classifier.
    """
    if mode not in ('intraday','longterm') or not isinstance(cost_bps,(int,float)) or isinstance(cost_bps,bool) or not math.isfinite(cost_bps) or not 0<=cost_bps<=1000:
        raise ValueError('Use intraday/longterm mode and a finite cost from 0 to 1000 bps')
    if bundle.get('synthetic'):return {'status':'NOT VALIDATED','reason':'Synthetic data cannot establish model skill.'}
    rows=completed_rows(bundle.get('candles',[]),mode,now);samples=dataset(rows,mode,cost_bps)
    if len(samples)<360:return {'status':'INSUFFICIENT HISTORY','samples':len(samples),'reason':'Need at least 360 labelled samples for three walk-forward folds.'}
    horizon=20 if mode=='longterm' else 6;start=max(180,len(samples)//2);step=(len(samples)-start)//3
    folds=[];all_returns=[];stress_returns=[]
    for fold in range(3):
        begin=start+fold*step;end=start+(fold+1)*step if fold<2 else len(samples)
        training=[r for r in samples[:begin] if r['target_t']<samples[begin]['t']]
        test=[r for r in samples[begin:end] if fold==2 or r['target_t']<samples[end]['t']]
        if len(training)<100 or len({r['y'] for r in training})<2 or not test:
            return {'status':'INSUFFICIENT DIVERSITY','reason':'All three folds require both training classes and valid unseen observations.'}
        model=fit(training);metrics=evaluate(model,test,horizon)
        selected=[];next_index=-1
        for row in test:
            if probability(model,row['x'])>=.65 and row['index']>=next_index:
                selected.append(row['return']);next_index=row['index']+horizon+1
        all_returns.extend(selected);stress_returns.extend(r-.002 for r in selected)
        folds.append({'fold':fold+1,'train_samples':len(training),'train_labels_end':datetime.fromtimestamp(training[-1]['target_t'],IST).isoformat(),**metrics})
    def outcomes(values):
        equity=1.;peak=1.;dd=0.
        for value in values:equity*=1+value;peak=max(peak,equity);dd=max(dd,1-equity/peak)
        wins=sum(v>0 for v in values)
        return {'simulated_trades':len(values),'net_win_rate':round(wins/len(values)*100,1) if values else None,
                'win_rate_95pct_interval':wilson(wins,len(values)),
                'mean_net_return_pct':round(statistics.mean(values)*100,3) if values else None,
                'compounded_return_pct':round((equity-1)*100,3),'max_drawdown_pct':round(dd*100,3)}
    total=sum(f['holdout_samples'] for f in folds)
    return {'status':'WALK-FORWARD RESEARCH','mode':mode,'folds':folds,'cost_bps':cost_bps,
        'test_samples':total,'direction_accuracy':round(sum(f['direction_accuracy']*f['holdout_samples'] for f in folds)/total*100,1),
        'baseline_accuracy':round(sum(f['baseline_accuracy']*f['holdout_samples'] for f in folds)/total*100,1),
        **outcomes(all_returns),'cost_stress':{'cost_bps':cost_bps+20,**outcomes(stress_returns)},
        'validated_for_live_trading':False,'created_at':now_ist().isoformat(),
        'notes':'Fixed 0.65 threshold; next-bar-open entry and horizon-close exit. Three purged expanding windows. Forecast labels overlap; simulated trades do not. Cost stress adds 20 bps to the same trades. No stop/target fills, news/OI training or corporate-action certification. Compounded returns assume reinvestment of equal strategy capital.'}
