# encode_token.py
import base64

try:
    with open("token.pickle", "rb") as token_file:
        encoded_string = base64.b64encode(token_file.read())
        print("--- COPIE O TEXTO ABAIXO ---")
        print(encoded_string.decode('utf-8'))
        print("--- FIM DO TEXTO ---")
except FileNotFoundError:
    print("ERRO: Arquivo 'token.pickle' não encontrado. Execute o bot.py localmente uma vez para criá-lo.")