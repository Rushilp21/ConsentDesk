"""Explicit, bounded sector/theme coverage. Membership is not an index or endorsement."""
SECTORS = {
    'healthcare': ('Healthcare', [('SUNPHARMA','Sun Pharmaceutical'),('CIPLA','Cipla'),('DRREDDY','Dr Reddys Laboratories'),('APOLLOHOSP','Apollo Hospitals'),('DIVISLAB','Divis Laboratories'),('LUPIN','Lupin')]),
    'energy': ('Energy & Power', [('RELIANCE','Reliance Industries'),('ONGC','Oil and Natural Gas Corporation'),('NTPC','NTPC'),('POWERGRID','Power Grid Corporation'),('TATAPOWER','Tata Power'),('COALINDIA','Coal India')]),
    'railways': ('Railways', [('IRCTC','Indian Railway Catering and Tourism Corporation'),('RVNL','Rail Vikas Nigam'),('IRFC','Indian Railway Finance Corporation'),('RAILTEL','RailTel Corporation'),('IRCON','Ircon International'),('BEML','BEML')]),
    'technology': ('Technology', [('TCS','Tata Consultancy Services'),('INFY','Infosys'),('HCLTECH','HCL Technologies'),('WIPRO','Wipro'),('PERSISTENT','Persistent Systems'),('COFORGE','Coforge')]),
    'banking': ('Banking & Finance', [('HDFCBANK','HDFC Bank'),('ICICIBANK','ICICI Bank'),('SBIN','State Bank of India'),('AXISBANK','Axis Bank'),('KOTAKBANK','Kotak Mahindra Bank'),('BAJFINANCE','Bajaj Finance')]),
    'automobiles': ('Automobiles', [('MARUTI','Maruti Suzuki'),('M&M','Mahindra and Mahindra'),('EICHERMOT','Eicher Motors'),('BAJAJ-AUTO','Bajaj Auto'),('TVSMOTOR','TVS Motor Company'),('HEROMOTOCO','Hero MotoCorp')]),
    'consumer': ('Consumer & Retail', [('ITC','ITC'),('HINDUNILVR','Hindustan Unilever'),('NESTLEIND','Nestle India'),('TITAN','Titan Company'),('TRENT','Trent'),('BRITANNIA','Britannia Industries')]),
    'metals': ('Metals & Mining', [('TATASTEEL','Tata Steel'),('JSWSTEEL','JSW Steel'),('HINDALCO','Hindalco Industries'),('VEDL','Vedanta'),('NMDC','NMDC'),('SAIL','Steel Authority of India')]),
    'defence': ('Defence', [('HAL','Hindustan Aeronautics'),('BEL','Bharat Electronics'),('BDL','Bharat Dynamics'),('MAZDOCK','Mazagon Dock Shipbuilders'),('COCHINSHIP','Cochin Shipyard'),('GRSE','Garden Reach Shipbuilders and Engineers')]),
    'infrastructure': ('Infrastructure & Realty', [('LT','Larsen and Toubro'),('DLF','DLF'),('ULTRACEMCO','UltraTech Cement'),('AMBUJACEM','Ambuja Cements'),('ADANIPORTS','Adani Ports'),('GODREJPROP','Godrej Properties')]),
}


def sector_value(value):
    if value not in ('all',*SECTORS):raise ValueError('Choose a supported sector')
    return value


def sector_for(symbol):
    return next((label for label,stocks in SECTORS.values() if any(s==symbol for s,_ in stocks)),'Unclassified')


def metadata(symbol):
    return next(({'name':name,'sector':label} for label,stocks in SECTORS.values() for s,name in stocks if s==symbol),{'name':symbol,'sector':'Unclassified'})


def catalogue():
    return [{'id':'all','label':'All sectors','symbols':[]},*[{'id':key,'label':label,'symbols':[s for s,_ in stocks]} for key,(label,stocks) in SECTORS.items()]]
