import json
import pandas as pd
import matplotlib.pyplot as plt

with open('results/threshold_shape.json') as f:
    q=json.load(f)['quintiles']
x=[r['quintile'] for r in q]
y=[r['fraction'] for r in q]
lo=[r['fraction']-r['wilson95'][0] for r in q]
hi=[r['wilson95'][1]-r['fraction'] for r in q]
plt.figure(figsize=(7,4.5))
plt.errorbar(x,y,yerr=[lo,hi],fmt='o-',capsize=4)
plt.xlabel('Квинтиль компактности')
plt.ylabel('Доля ядерных X-ray источников')
plt.ylim(0,1.05)
plt.xticks(x)
plt.tight_layout()
plt.savefig('results/amuse_activity_vs_compactness.png',dpi=180)
plt.close()

with open('results/bh_host_summary.json') as f:
    c=json.load(f)['correlations']
labels=['K-светимость','Радиус','Компактность','Дисперсия','Концентрация']
keys=['BH_vs_Lk','BH_vs_Re','BH_vs_compactness','BH_vs_sigma','BH_vs_C28']
vals=[c[k]['rho'] for k in keys]
plt.figure(figsize=(8,4.5))
plt.bar(labels,vals)
plt.ylabel('Spearman rho с массой SMBH')
plt.xticks(rotation=20,ha='right')
plt.tight_layout()
plt.savefig('results/bh_mass_correlations.png',dpi=180)
plt.close()
