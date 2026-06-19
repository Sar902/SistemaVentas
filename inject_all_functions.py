import os
import django
import re

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), 'backend', 'config'))
django.setup()

from django.db import connection, transaction

def detect_and_read(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        return f.read()

sql_content = detect_and_read(r"C:\Users\ashle\Downloads\ADMINISTRADORES_BD\ADMINISTRADORES\bendicion_de_dios.sql")

# Regex to match functions
func_pattern = re.compile(
    r"CREATE\s+FUNCTION\s+public\..+?\s+AS\s+\$\$.*?\$\$\s*LANGUAGE\s+\w+([^\n;]*;)?",
    re.DOTALL | re.IGNORECASE
)

# Regex to match procedures
proc_pattern = re.compile(
    r"CREATE\s+PROCEDURE\s+public\..+?\s+AS\s+\$\$.*?\$\$\s*LANGUAGE\s+\w+([^\n;]*;)?",
    re.DOTALL | re.IGNORECASE
)

functions = [m.group(0) for m in func_pattern.finditer(sql_content)]
procedures = [m.group(0) for m in proc_pattern.finditer(sql_content)]

print(f"Extracted {len(functions)} functions and {len(procedures)} procedures from SQL file.")

errors = []
success_count = 0

with connection.cursor() as cursor:
    # We will run each one in its own sub-transaction or handle errors individually
    for index, ddl in enumerate(functions + procedures, 1):
        # Convert CREATE to CREATE OR REPLACE for safety and idempotency
        ddl_clean = re.sub(r"^CREATE\s+FUNCTION", "CREATE OR REPLACE FUNCTION", ddl, flags=re.IGNORECASE)
        ddl_clean = re.sub(r"^CREATE\s+PROCEDURE", "CREATE OR REPLACE PROCEDURE", ddl_clean, flags=re.IGNORECASE)
        
        # Get the first line for descriptive logging
        first_line = ddl_clean.split('\n')[0].strip()
        print(f"[{index}] Running: {first_line}")
        
        try:
            # Execute DDL
            with transaction.atomic():
                cursor.execute(ddl_clean)
            print(f"    -> SUCCESS")
            success_count += 1
        except Exception as e:
            print(f"    -> ERROR: {e}")
            errors.append((first_line, str(e)))

print(f"\nMigration finished: {success_count} succeeded, {len(errors)} failed.")
if errors:
    print("\nErrors encountered:")
    for f, err in errors:
        print(f"- {f}\n  Error: {err}")
else:
    print("\nAll database functions successfully injected!")
