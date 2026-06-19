import re

def detect_and_read(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        return f.read()

sql_content = detect_and_read(r"C:\Users\ashle\Downloads\ADMINISTRADORES_BD\ADMINISTRADORES\bendicion_de_dios.sql")

# Let's find all CREATE FUNCTION declarations. 
# We look for a pattern like:
# CREATE FUNCTION public.xxxx(
#   ...
# ) ...
# AS $$
# ...
# $$;
# Or with LANGUAGE at the end:
# $$
# LANGUAGE plpgsql;

# Let's write a regex that matches from CREATE FUNCTION until we hit a line that doesn't start with space/comment and is a new statement, 
# or look for "$$ LANGUAGE plpgsql" or similar.
# In pg_dump, it's:
# CREATE FUNCTION public.name(...) AS $$
# ...
# $$
# LANGUAGE plpgsql ... ;

matches = re.finditer(r"CREATE\s+FUNCTION\s+public\..+?\s+AS\s+\$\$.*?\$\$\s*LANGUAGE\s+\w+([^\n;]*;)?", sql_content, re.DOTALL | re.IGNORECASE)

count = 0
for match in matches:
    print(f"\n================ FUNCTION {count+1} ================")
    print(match.group(0))
    count += 1
    if count >= 3:
        break
