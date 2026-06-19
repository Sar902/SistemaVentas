import re

def detect_and_read(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        return f.read()

sql_content = detect_and_read(r"C:\Users\ashle\Downloads\ADMINISTRADORES_BD\ADMINISTRADORES\bendicion_de_dios.sql")

func_pattern = re.compile(
    r"CREATE\s+FUNCTION\s+public\..+?\s+AS\s+\$\$.*?\$\$\s*LANGUAGE\s+\w+([^\n;]*;)?",
    re.DOTALL | re.IGNORECASE
)

for i, match in enumerate(func_pattern.finditer(sql_content), 1):
    start_pos = match.start()
    line_no = sql_content.count('\n', 0, start_pos) + 1
    decl = match.group(0).split("AS $$")[0].strip()
    # Replace newlines for compact output
    decl_compact = " ".join(decl.split())
    print(f"Index {i} (Line {line_no}): {decl_compact}")
