import re

def detect_and_read(filepath):
    for encoding in ['utf-8', 'utf-16', 'utf-16-le', 'utf-16-be', 'latin-1']:
        try:
            with open(filepath, 'r', encoding=encoding) as f:
                content = f.read()
                if "sp_productos_sin_movimiento" in content:
                    print(f"Successfully read with encoding: {encoding}")
                    return content
        except Exception:
            pass
    raise Exception("Could not read file with any tested encoding")

sql_content = detect_and_read(r"C:\Users\ashle\Downloads\ADMINISTRADORES_BD\ADMINISTRADORES\bendicion_de_dios.sql")

# We want to find function definitions.
# In pg_dump, a function is typically:
# CREATE FUNCTION public.name(...) RETURNS ...
#     AS $$
#     ...
#     $$;
# Let's find all occurences of CREATE FUNCTION and capture until the end of the definition.
# A definition usually ends with language specification like "$$ LANGUAGE plpgsql;" or similar.
# Let's find all CREATE FUNCTION blocks.

print("Searching for functions...")
matches = re.finditer(r"CREATE\s+FUNCTION\s+public\..+?\s+AS\s+\$\$(.*?)\$\$;", sql_content, re.DOTALL | re.IGNORECASE)

count = 0
for match in matches:
    full_declaration = match.group(0)
    # let's look at the header
    header = full_declaration.split("AS")[0].strip()
    print(f"\n--- Found Function {count+1}: {header} ---")
    count += 1

print(f"\nTotal functions found with regex: {count}")
