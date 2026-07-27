from astroquery.vizier import Vizier
v=Vizier(columns=['**'],row_limit=-1)
t=v.get_catalogs('J/ApJ/831/134/table2')[0]
for c in ['logBHMass','logsigma','logLk','logRe','C28','Dist']:
    col=t[c]
    print(c,'unit=',col.unit,'desc=',col.description,'minmax=',(col.min(),col.max()))
