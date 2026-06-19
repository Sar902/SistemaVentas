with open(r"C:\Users\ashle\Downloads\ADMINISTRADORES_BD\ADMINISTRADORES\bendicion_de_dios.sql", 'r', encoding='utf-8') as f:
    for i, line in enumerate(f):
        if "sp_top_productos" in line:
            print(f"Line {i+1}: {line.strip()}")
