import json
import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.stats import spearmanr

df=pd.read_csv('data/bh_main.csv')
for c in ['logBHMass','logsigma','logLk','logRe','C28','Dist']:
    df[c]=pd.to_numeric(df[c],errors='coerce')
df=df[(df.logBHMass>0)&df.logLk.notna()&df.logRe.notna()].copy()
df['lum_compactness']=df.logLk-2*df.logRe

def corr(a,b):
    d=df[[a,b]].dropna(); r,p=spearmanr(d[a],d[b])
    return {'n':len(d),'rho':float(r),'p':float(p)}

def ols(cols):
    d=df[['logBHMass']+cols].dropna().copy()
    X=d[cols]
    X=(X-X.mean())/X.std(ddof=0)
    X=sm.add_constant(X)
    m=sm.OLS(d.logBHMass,X).fit(cov_type='HC3')
    return {'n':len(d),'r2':float(m.rsquared),'aic':float(m.aic),'coef':m.params.to_dict(),'se_hc3':m.bse.to_dict(),'p_hc3':m.pvalues.to_dict()}

res={
 'sample_n':len(df),
 'correlations':{
  'BH_vs_Lk':corr('logBHMass','logLk'),
  'BH_vs_Re':corr('logBHMass','logRe'),
  'BH_vs_compactness':corr('logBHMass','lum_compactness'),
  'BH_vs_sigma':corr('logBHMass','logsigma'),
  'BH_vs_C28':corr('logBHMass','C28')},
 'models':{
  'Lk':ols(['logLk']),
  'compactness':ols(['lum_compactness']),
  'Lk_Re':ols(['logLk','logRe']),
  'Lk_compactness':ols(['logLk','lum_compactness']),
  'sigma':ols(['logsigma']),
  'sigma_compactness':ols(['logsigma','lum_compactness'])}
}
with open('results/bh_host_summary.json','w') as f: json.dump(res,f,indent=2)
df.to_csv('results/bh_hosts_derived.csv',index=False)
print(json.dumps(res,indent=2))
