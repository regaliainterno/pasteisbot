# encode_para_arquivo.py
import base64

try:
    # Lê o token.pickle
    with open("token.pickle", "rb") as token_file:
        encoded_string = base64.b64encode(token_file.read()).decode('utf-8')

    # Salva a string resultante em um novo arquivo de texto
    with open("token_em_base64.txt", "w") as output_file:
        output_file.write(encoded_string)

    print("\n✅ SUCESSO! ✅")
    print("A string Base64 foi salva no arquivo 'token_em_base64.txt'.")
    print("\nAbra esse arquivo, copie TODO o conteúdo e cole no Railway.")

except FileNotFoundError:
    print("ERRO: Arquivo 'token.pickle' não encontrado. Execute o bot.py localmente uma vez para criá-lo.")