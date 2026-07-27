from astroquery.vizier import Vizier
viz=Vizier(columns=['**'], row_limit=-1)
for cat in ['J/ApJ/714/25']:
    ts=viz.get_catalogs(cat)
    print(cat, len(ts))
    for i,t in enumerate(ts):
        print(i, len(t), t.colnames)
        t.write(f'data/amuse_virgo_{i}.csv', format='ascii.csv', overwrite=True)
