import json
import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.stats import fisher_exact, spearmanr
from statsmodels.stats.proportion import proportion_confint

am=pd.read_csv('data/amuse_virgo_0.csv')
st=pd.read_csv('data/acsvcs_struct.csv')
# VizieR exports masked text fields as blanks.
am['active']=(am['l_logLx'].fillna('').astype(str).str.strip()!='<').astype(int)
# Require numerical stellar mass and structural match.
for c in ['logM*','Dist','logLx']:
    am[c]=pd.to_numeric(am[c],errors='coerce')
for c in ['ACSVCS','rez','reg','<muz>','<mug>','n.z','n.g']:
    if c in st: st[c]=pd.to_numeric(st[c],errors='coerce')

df=am.merge(st,on='ACSVCS',how='left',suffixes=('','_s'))
# z-band effective radius is arcsec; convert to kpc using measured distance.
arcsec_to_rad=np.pi/(180*3600)
df['Re_kpc']=df['rez']*df['Dist']*1000*arcsec_to_rad
# Empirical compactness index; additive constant omitted.
df['compactness']=df['logM*']-2*np.log10(df['Re_kpc'])

# Helper

def frac_ci(mask):
    x=int(df.loc[mask,'active'].sum()); n=int(mask.sum())
    lo,hi=proportion_confint(x,n,alpha=0.05,method='wilson') if n else (np.nan,np.nan)
    return {'n':n,'active':x,'fraction':x/n if n else None,'wilson95':[lo,hi]}

res={}
res['sample']={'amuse_rows':len(am),'structural_matches':int(df['Re_kpc'].notna().sum()),'active_total':int(df['active'].sum())}
res['mass_bins']={
    'logM_lt_9':frac_ci(df['logM*']<9),
    'logM_9_10':frac_ci((df['logM*']>=9)&(df['logM*']<10)),
    'logM_ge_10':frac_ci(df['logM*']>=10),
    'logM_ge_10_5':frac_ci(df['logM*']>=10.5),
}
valid=df['compactness'].notna() & df['logM*'].notna()
q50=float(df.loc[valid,'compactness'].quantile(.5)); q75=float(df.loc[valid,'compactness'].quantile(.75))
res['compactness_thresholds']={'median':q50,'q75':q75}
res['compactness_bins']={
    'below_median':frac_ci(valid & (df['compactness']<q50)),
    'above_median':frac_ci(valid & (df['compactness']>=q50)),
    'top_quartile':frac_ci(valid & (df['compactness']>=q75)),
    'massive_and_top_quartile':frac_ci(valid & (df['logM*']>=10) & (df['compactness']>=q75)),
}
# Surface brightness: smaller magnitude = brighter/denser projected light.
if '<muz>' in df:
    sb_valid=df['<muz>'].notna()
    sb_q25=float(df.loc[sb_valid,'<muz>'].quantile(.25))
    res['surface_brightness']={'bright_q25_cut_mag_arcsec2':sb_q25,
        'brightest_quartile':frac_ci(sb_valid & (df['<muz>']<=sb_q25))}

# Logistic models with standardized predictors.
def fit_logit(cols):
    d=df[['active']+cols].dropna().copy()
    X=d[cols].copy()
    X=(X-X.mean())/X.std(ddof=0)
    X=sm.add_constant(X)
    m=sm.GLM(d['active'],X,family=sm.families.Binomial()).fit()
    return {'n':len(d),'coef':m.params.to_dict(),'se':m.bse.to_dict(),'p':m.pvalues.to_dict(),'aic':float(m.aic)}
res['logit_mass']=fit_logit(['logM*'])
res['logit_compactness']=fit_logit(['compactness'])
res['logit_mass_compactness']=fit_logit(['logM*','compactness'])
# Direct comparison high/low mass
ct=pd.crosstab(df['logM*']>=10,df['active']).reindex(index=[False,True],columns=[0,1],fill_value=0)
res['fisher_mass_ge10']={'table':ct.values.tolist(),'oddsratio':float(fisher_exact(ct.values)[0]),'p':float(fisher_exact(ct.values)[1])}
# Rank correlations only among detections for luminosity trends
act=df[df.active.eq(1)]
if len(act)>2:
    rho,p=spearmanr(act['logM*'],act['logLx'],nan_policy='omit')
    res['active_logLx_vs_mass_spearman']={'rho':float(rho),'p':float(p),'n':int(act[['logM*','logLx']].dropna().shape[0])}

cols=['ACSVCS','VCC','OName','logM*','active','logLx','Re_kpc','compactness','<muz>','n.z']
df[cols].to_csv('results/amuse_merged.csv',index=False)
with open('results/amuse_summary.json','w') as f: json.dump(res,f,indent=2,ensure_ascii=False)
print(json.dumps(res,indent=2,ensure_ascii=False))
