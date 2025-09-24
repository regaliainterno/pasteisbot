# config.py

"""
Configurações e constantes globais do PasteisBot.

Este módulo centraliza todas as variáveis de ambiente, nomes de arquivos e constantes do negócio.
Qualquer ajuste de sabores, preços ou arquivos deve ser feito aqui.

Sugestão: Para adicionar novos sabores, basta atualizar SABORES_VALIDOS.
"""

import os
from enum import Enum

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

# Para adicionar novos sabores, basta incluir na lista abaixo!
SABORES_VALIDOS = ['carne', 'frango']

TIMEZONE = 'America/Sao_Paulo'

# --- ESTADOS DA CONVERSA (para o comando /fechamento) ---
class ConversaEstado(Enum):
    ASK_CARRYOVER = 1

# Mantém compatibilidade com ConversationHandler do Telegram
ASK_CARRYOVER = range(1)