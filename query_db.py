import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), 'backend', 'config'))
django.setup()

from django.db import connection

print("Checking database tables...")
with connection.cursor() as cursor:
    cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
    tables = cursor.fetchall()
    print("Tables in database:", [t[0] for t in tables])

print("\nChecking all custom functions/procedures in public schema...")
query = """
SELECT ns.nspname, p.proname, pg_get_function_arguments(p.oid)
FROM pg_proc p
JOIN pg_namespace ns ON p.pronamespace = ns.oid
WHERE ns.nspname = 'public'
"""
with connection.cursor() as cursor:
    cursor.execute(query)
    rows = cursor.fetchall()
    for row in rows:
        print(f"Schema: {row[0]}, Function: {row[1]}, Arguments: {row[2]}")
