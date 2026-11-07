import duckdb
from dd.config import DUCKDB_PATH

con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
df = con.execute("SELECT label, concept, parent_concept, parent_abstract_concept FROM income_statement WHERE label = 'Services'").fetchdf()
con.close()
print(df.to_string())