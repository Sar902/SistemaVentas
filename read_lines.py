with open(r"C:\Users\ashle\Downloads\ADMINISTRADORES_BD\ADMINISTRADORES\bendicion_de_dios.sql", 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i in range(710, 746):
    if i < len(lines):
        print(f"{i+1}: {lines[i]}", end="")
