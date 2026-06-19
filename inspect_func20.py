import re

def detect_and_read(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        return f.read()

sql_content = detect_and_read(r"C:\Users\ashle\Downloads\ADMINISTRADORES_BD\ADMINISTRADORES\bendicion_de_dios.sql")

func_pattern = re.compile(
    r"CREATE\s+FUNCTION\s+public\..+?\s+AS\s+\$\$.*?\$\$\s*LANGUAGE\s+\w+([^\n;]*;)?",
    re.DOTALL | re.IGNORECASE
)

functions = [m.group(0) for m in func_pattern.finditer(sql_content)]

if len(functions) > 19:
    print("=== FUNCTION 20 PARSED TEXT ===")
    print(functions[19])
