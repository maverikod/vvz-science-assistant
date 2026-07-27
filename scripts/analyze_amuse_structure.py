import json
import numpy as np
import pandas as pd
import statsmodels.api as sm

df=pd.read_csv('results/amuse_merged.csv')
df['brightness_z']=-pd.to_numeric(df['<muz>'],errors='coerce')
df['sersic_n']=pd.to_numeric(df['n.z'],errors='coerce')

def fit(cols):
    d=df[['active']+cols].dropna().copy()
    X=d[cols]
    X=(X-X.mean())/X.std(ddof=0)
    X=sm.add_constant(X)
    m=sm.GLM(d.active,X,family=sm.families.Binomial()).fit()
    return {'n':len(d),'aic':float(m.aic),'coef':m.params.to_dict(),'se':m.bse.to_dict(),'p':m.pvalues.to_dict()}
res={
 'mass_brightness':fit(['logM*','brightness_z']),
 'mass_sersic':fit(['logM*','sersic_n']),
 'mass_brightness_sersic':fit(['logM*','brightness_z','sersic_n']),
 'mass_compactness_sersic':fit(['logM*','compactness','sersic_n']),
 'brightness_sersic':fit(['brightness_z','sersic_n'])
}
with open('results/amuse_structure_models.json','w') as f:json.dump(res,f,indent=2)
print(json.dumps(res,indent=2))
