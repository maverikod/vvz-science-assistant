from astroquery.vizier import Vizier
viz=Vizier(columns=['**'], row_limit=-1)
for cat,name in [('J/ApJS/164/334/struct','acsvcs_struct'),('J/ApJS/164/334/acsvcs','acsvcs_global')]:
    t=viz.get_catalogs(cat)[0]
    print(name,len(t),t.colnames)
    t.write(f'data/{name}.csv',format='ascii.csv',overwrite=True)
