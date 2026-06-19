def detect_and_read(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        return f.read()

sql_content = detect_and_read(r"C:\Users\ashle\Downloads\ADMINISTRADORES_BD\ADMINISTRADORES\bendicion_de_dios.sql")

statements = []
current_stmt = []
in_function = False

for line in sql_content.splitlines():
    if not in_function:
        if line.strip().startswith(("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", "CREATE PROCEDURE", "CREATE OR REPLACE PROCEDURE")):
            in_function = True
            current_stmt = [line]
    else:
        current_stmt.append(line)
        if line.strip().endswith("$$;"):
            statements.append("\n".join(current_stmt))
            in_function = False

print(f"Extracted {len(statements)} functions using simple parser.")
for i, stmt in enumerate(statements, 1):
    first_line = stmt.split('\n')[0].strip()
    print(f"Index {i}: {first_line}")
    # Let's print the last line to verify it ends with $$;
    last_line = stmt.split('\n')[-1].strip()
    print(f"   Ends with: {last_line}")
