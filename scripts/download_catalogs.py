from astroquery.vizier import Vizier
from astropy.table import Table

viz = Vizier(columns=['**'], row_limit=-1)
catalogs = {
    'sparc': 'J/AJ/152/157/table1',
    'bh_main': 'J/ApJ/831/134/table2',
    'bh_incomplete': 'J/ApJ/831/134/table3',
}
for name, cat in catalogs.items():
    tables = viz.get_catalogs(cat)
    if not tables:
        raise RuntimeError(f'No table returned for {cat}')
    t = tables[0]
    t.write(f'data/{name}.ecsv', format='ascii.ecsv', overwrite=True)
    t.write(f'data/{name}.csv', format='ascii.csv', overwrite=True)
    print(name, len(t), t.colnames)
