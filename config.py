"""Explicit, hand-curated watchlist. Not representative of any sector index."""
ETF = {'SPY': 'S&P500 benchmark', 'QQQ': 'Nasdaq-100 benchmark', 'IGV': 'Broad US software ETF (not AI-only)', 'SMH': 'Semiconductor ETF (not all hardware)'}
SOFTWARE = {
    'CRWD': ('CrowdStrike','사이버보안'), 'NET': ('Cloudflare','네트워크·엣지 SW'),
    'MSFT': ('Microsoft','종합 플랫폼'), 'APP': ('AppLovin','광고·AI 응용'),
    'IOT': ('Samsara','IoT·운영 SW'), 'ESTC': ('Elastic','검색·데이터'),
    'PANW': ('Palo Alto Networks','사이버보안'), 'CRM': ('Salesforce','업무 AI'),
    'PLTR': ('Palantir','기업 AI'), 'SNOW': ('Snowflake','데이터 플랫폼'),
    'PATH': ('UiPath','업무자동화'), 'DDOG': ('Datadog','클라우드 관측'),
    'NOW': ('ServiceNow','기업 업무 SW')}
HARDWARE = {
    'ANET': ('Arista Networks','데이터센터 네트워킹'),
    'AMD': ('AMD','반도체'), 'AVGO': ('Broadcom','반도체·인프라 SW'),
    'TSM': ('TSMC ADR','반도체 파운드리·ADR'), 'MU': ('Micron','메모리 반도체'),
    'NVDA': ('NVIDIA','AI 가속기·반도체')}
TICKERS = tuple(ETF) + tuple(SOFTWARE) + tuple(HARDWARE)
GROUPS = {t: 'SW' for t in SOFTWARE} | {t: 'HW' for t in HARDWARE}
# ANET is not a semiconductor constituent: using QQQ (broad technology comparator), not SMH.
COMPARATOR = {t: ('QQQ' if t == 'ANET' else 'SMH') for t in HARDWARE} | {t: 'IGV' for t in SOFTWARE}
