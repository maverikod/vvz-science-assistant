import json
import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.stats.proportion import proportion_confint

df=pd.read_csv('results/amuse_merged.csv')
valid=df[['active','compactness']].dropna().copy()
valid['quintile']=pd.qcut(valid.compactness,5,labels=False,duplicates='drop')
rows=[]
for q,g in valid.groupby('quintile'):
    n=len(g); x=int(g.active.sum()); lo,hi=proportion_confint(x,n,method='wilson')
    rows.append({'quintile':int(q)+1,'n':n,'active':x,'fraction':x/n,'compactness_min':float(g.compactness.min()),'compactness_max':float(g.compactness.max()),'wilson95':[float(lo),float(hi)]})
# Linear vs quadratic logit on standardized compactness.
z=(valid.compactness-valid.compactness.mean())/valid.compactness.std(ddof=0)
X1=sm.add_constant(pd.DataFrame({'z':z}))
X2=sm.add_constant(pd.DataFrame({'z':z,'z2':z*z}))
m1=sm.GLM(valid.active,X1,family=sm.families.Binomial()).fit()
m2=sm.GLM(valid.active,X2,family=sm.families.Binomial()).fit()
# exploratory best single cut, min 15 each side
best=None
vals=np.sort(valid.compactness.unique())
for c in (vals[:-1]+vals[1:])/2:
    left=valid[valid.compactness<c]; right=valid[valid.compactness>=c]
    if len(left)<15 or len(right)<15: continue
    p1=np.clip(left.active.mean(),1e-9,1-1e-9); p2=np.clip(right.active.mean(),1e-9,1-1e-9)
    ll=(left.active*np.log(p1)+(1-left.active)*np.log(1-p1)).sum()+(right.active*np.log(p2)+(1-right.active)*np.log(1-p2)).sum()
    aic=2*3-2*ll
    item={'cut':float(c),'left_n':len(left),'left_fraction':float(p1),'right_n':len(right),'right_fraction':float(p2),'aic':float(aic)}
    if best is None or aic<best['aic']: best=item
res={'quintiles':rows,'linear_logit':{'aic':float(m1.aic),'coef':m1.params.to_dict(),'p':m1.pvalues.to_dict()},'quadratic_logit':{'aic':float(m2.aic),'coef':m2.params.to_dict(),'p':m2.pvalues.to_dict()},'exploratory_step':best}
with open('results/threshold_shape.json','w') as f:json.dump(res,f,indent=2)
print(json.dumps(res,indent=2))
