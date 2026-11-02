import duckdb
from dd.config import DUCKDB_PATH

con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
df = con.execute("SELECT * FROM balance_sheet WHERE label = 'Total assets'").fetchdf()
con.close()
print(list(df.columns))