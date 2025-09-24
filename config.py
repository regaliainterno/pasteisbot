# config.py
import os

# --- CONFIGURAÇÕES DE AMBIENTE ---
# Lê as "senhas" das variáveis de ambiente do Railway
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID", "")

# --- NOMES DOS ARQUIVOS NO DRIVE ---
DRIVE_VENDAS_FILE = "vendas_pasteis.csv"
DRIVE_ESTOQUE_FILE = "estoque_diario.csv"
DRIVE_CONSUMO_FILE = "consumo_pessoal.csv"
DRIVE_FECHAMENTOS_FILE = "historico_fechamentos.csv"

# --- CONFIGURAÇÕES DO NEGÓCIO ---
PRECO_FIXO_VENDA = 10.00
PRECO_FIXO_CUSTO = 4.50
SABORES_VALIDOS = ['carne', 'frango']
TIMEZONE = 'America/Sao_Paulo'

# --- ESTADOS DA CONVERSA (para o comando /fechamento) ---
ASK_CARRYOVER = range(1)