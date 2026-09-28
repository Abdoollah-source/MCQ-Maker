"""Print recent AI failure summaries without reading question content or API keys."""
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as connection:
    rows = connection.execute(
        "SELECT created_utc, source_path, error_summary FROM history "
        "WHERE source_mode = 'ai' ORDER BY created_utc DESC LIMIT 30"
    ).fetchall()
for row in rows:
    print(' | '.join(map(str, row)))
