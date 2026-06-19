with open(r"C:\Users\ashle\Downloads\ADMINISTRADORES_BD\ADMINISTRADORES\bendicion_de_dios.sql", 'r', encoding='utf-8') as f:
    content = f.read()

import re
matches = re.finditer(r"CREATE\s+FUNCTION\s+public\.sp_top_productos\(.*?\)\s+RETURNS\s+TABLE\(.*?\)", content, re.IGNORECASE | re.DOTALL)
for m in matches:
    print(m.group(0))
