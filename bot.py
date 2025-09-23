import os
import pandas as pd
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, ContextTypes
import pickle
import json
import base64
import io
import traceback
from datetime import datetime, timedelta

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

# --- CONFIGURAÇÕES LENDO DE VARIÁVEIS DE AMBIENTE ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
DRIVE_FILE_NAME = "vendas_pasteis.csv"
DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID", "")

# --- CONFIGURAÇÕES DO NEGÓCIO ---
PRECO_FIXO_VENDA = 10.00
PRECO_FIXO_CUSTO = 4.50
SABORES_VALIDOS = ['carne', 'frango']
TIMEZONE = 'America/Sao_Paulo'

# --- FUNÇÕES DO GOOGLE DRIVE ---
SCOPES = ['https://www.googleapis.com/auth/drive']


def get_drive_service():
    creds = None
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if os.path.exists('credentials.json'):
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                creds = flow.run_local_server(port=0)
            else:
                google_token_base64 = os.environ.get('GOOGLE_TOKEN_BASE64')
                if google_token_base64:
                    decoded_token = base64.b64decode(google_token_base64)
                    creds = pickle.loads(decoded_token)
                else:
                    raise ValueError("Token ou credenciais não encontrados.")
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)
    return build('drive', 'v3', credentials=creds)


def get_file_id(service, file_name, folder_id):
    query = f"name='{file_name}' and trashed=false"
    if folder_id:
        query += f" and '{folder_id}' in parents"
    response = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
    files = response.get('files', [])
    return files[0]['id'] if files else None


def download_dataframe(service, file_id):
    COLUNAS = ['data_hora', 'sabor', 'quantidade', 'preco_unidade', 'custo_unidade', 'total_venda', 'lucro_venda']
    if not file_id:
        return pd.DataFrame(columns=COLUNAS)
    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    fh.seek(0)
    try:
        df = pd.read_csv(fh)
        df['data_hora'] = pd.to_datetime(df['data_hora'])
        if 'lucro_venda' not in df.columns:
            df['custo_unidade'] = PRECO_FIXO_CUSTO
            df['lucro_venda'] = df['total_venda'] - (df['quantidade'] * PRECO_FIXO_CUSTO)
        return df
    except (pd.errors.EmptyDataError, KeyError):
        return pd.DataFrame(columns=COLUNAS)


def upload_dataframe(service, df, file_name, file_id, folder_id):
    csv_bytes = df.to_csv(index=False).encode('utf-8')
    fh = io.BytesIO(csv_bytes)
    media = MediaIoBaseUpload(fh, mimetype='text/csv', resumable=True)
    file_metadata = {'name': file_name}
    if folder_id and not file_id:
        file_metadata['parents'] = [folder_id]
    if file_id:
        service.files().update(fileId=file_id, media_body=media).execute()
    else:
        service.files().create(body=file_metadata, media_body=media, fields='id').execute()


# --- NOVOS COMANDOS DE RELATÓRIO (COM CORREÇÃO DE FUSO HORÁRIO) ---

async def relatorio_diario(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if context.args:
            data_filtro = pd.to_datetime(context.args[0]).date()
            titulo_relatorio = f"📊 *Dashboard do Dia {data_filtro.strftime('%d/%m/%Y')}*"
        else:
            data_filtro = pd.Timestamp.now(tz=TIMEZONE).date()
            titulo_relatorio = "📊 *Dashboard de Hoje*"

        await update.message.reply_text(f"Gerando relatório para {data_filtro.strftime('%d/%m/%Y')}...")
        service = get_drive_service()
        file_id = get_file_id(service, DRIVE_FILE_NAME, DRIVE_FOLDER_ID)
        df = download_dataframe(service, file_id)
        if df.empty:
            await update.message.reply_text("Nenhuma venda encontrada para gerar relatórios.")
            return

        # ----- CORREÇÃO APLICADA AQUI -----
        df_dia = df[df['data_hora'].dt.tz_convert(TIMEZONE).dt.date == data_filtro]

        if df_dia.empty:
            await update.message.reply_text(f"Nenhuma venda registrada no dia {data_filtro.strftime('%d/%m/%Y')}.")
            return
        total_pasteis = df_dia['quantidade'].sum()
        faturamento_bruto = df_dia['total_venda'].sum()
        custo_total = df_dia['custo_unidade'].multiply(df_dia['quantidade']).sum()
        lucro_liquido = df_dia['lucro_venda'].sum()
        relatorio_texto = f"{titulo_relatorio}\n\n"
        relatorio_texto += f"🥟 Pastéis Vendidos: *{int(total_pasteis)}*\n"
        relatorio_texto += f"💰 Faturamento Bruto: *R$ {faturamento_bruto:.2f}*\n"
        relatorio_texto += f"📉 Custo Total: *R$ {custo_total:.2f}*\n"
        relatorio_texto += f"🚀 Lucro Líquido: *R$ {lucro_liquido:.2f}*\n"
        await update.message.reply_text(relatorio_texto, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"Erro ao gerar relatório diário: {e}")


async def relatorio_lucro_periodo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text("❌ Erro! Use o formato: `/lucro [dias]`\nExemplo: `/lucro 7`")
            return
        dias = int(context.args[0])
        hoje = pd.Timestamp.now(tz=TIMEZONE).date()
        # ----- CORREÇÃO NO CÁLCULO DE DIAS -----
        data_inicio = hoje - timedelta(days=dias - 1)

        await update.message.reply_text(f"Gerando relatório de lucro dos últimos {dias} dias...")
        service = get_drive_service()
        file_id = get_file_id(service, DRIVE_FILE_NAME, DRIVE_FOLDER_ID)
        df = download_dataframe(service, file_id)
        if df.empty:
            await update.message.reply_text("Nenhuma venda encontrada para gerar relatórios.")
            return

        # ----- CORREÇÃO APLICADA AQUI -----
        df_periodo = df[(df['data_hora'].dt.tz_convert(TIMEZONE).dt.date >= data_inicio) & (
                    df['data_hora'].dt.tz_convert(TIMEZONE).dt.date <= hoje)]

        if df_periodo.empty:
            await update.message.reply_text(f"Nenhuma venda registrada nos últimos {dias} dias.")
            return
        lucro_total_periodo = df_periodo['lucro_venda'].sum()
        relatorio_texto = (f"📈 *Lucro dos Últimos {dias} Dias*\n"
                           f"_{data_inicio.strftime('%d/%m/%Y')} a {hoje.strftime('%d/%m/%Y')}_\n\n"
                           f"🚀 Lucro Líquido Total: *R$ {lucro_total_periodo:.2f}*")
        await update.message.reply_text(relatorio_texto, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"Erro ao gerar relatório de período: {e}")


async def enviar_csv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await update.message.reply_text("Buscando o arquivo de vendas no Drive...")
        service = get_drive_service()
        file_id = get_file_id(service, DRIVE_FILE_NAME, DRIVE_FOLDER_ID)
        if not file_id:
            await update.message.reply_text("Nenhum arquivo de vendas encontrado.")
            return
        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        fh.seek(0)
        await update.message.reply_document(document=InputFile(fh, filename=DRIVE_FILE_NAME),
                                            caption="Aqui está o seu relatório de vendas completo.")
    except Exception as e:
        await update.message.reply_text(f"Ocorreu um erro ao enviar o arquivo: {e}")


# --- COMANDOS PRINCIPAIS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_name = update.effective_user.first_name
    await update.message.reply_text(
        f'Olá, {user_name}! Seu assistente de vendas foi atualizado!\n\n'
        f'O preço de venda é *R$ {PRECO_FIXO_VENDA:.2f}* e o de custo é *R$ {PRECO_FIXO_CUSTO:.2f}*.\n\n'
        '**📋 Comandos Disponíveis:**\n\n'
        '**/venda [sabor] [qtd]**\n'
        '_(Registra uma nova venda. Ex: /venda frango 3)_\n\n'
        '**/diario**\n'
        '_(Relatório de vendas e lucro de hoje)_\n\n'
        '**/diario AAAA-MM-DD**\n'
        '_(Relatório de um dia específico. Ex: /diario 2025-09-22)_\n\n'
        '**/lucro [dias]**\n'
        '_(Lucro dos últimos dias. Ex: /lucro 7)_\n\n'
        '**/vendas**\n'
        '_(Envia o arquivo .csv com todas as vendas)_',
        parse_mode='Markdown'
    )


async def registrar_venda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if len(context.args) != 2:
            raise ValueError("Formato incorreto")
        sabor = context.args[0].lower()
        quantidade = int(context.args[1])
        if sabor not in SABORES_VALIDOS:
            sabores_str = ", ".join(SABORES_VALIDOS)
            await update.message.reply_text(f"❌ Sabor inválido. Use: *{sabores_str}*.", parse_mode='Markdown')
            return
        preco_unidade = PRECO_FIXO_VENDA
        custo_unidade = PRECO_FIXO_CUSTO
        total_venda = quantidade * preco_unidade
        lucro_venda = total_venda - (quantidade * custo_unidade)
        await update.message.reply_text("Registrando venda...")
        service = get_drive_service()
        file_id = get_file_id(service, DRIVE_FILE_NAME, DRIVE_FOLDER_ID)
        df = download_dataframe(service, file_id)
        nova_venda = pd.DataFrame([{
            'data_hora': pd.to_datetime('now', utc=True),
            'sabor': sabor,
            'quantidade': quantidade,
            'preco_unidade': preco_unidade,
            'custo_unidade': custo_unidade,
            'total_venda': total_venda,
            'lucro_venda': lucro_venda
        }])
        df = pd.concat([df, nova_venda], ignore_index=True)
        upload_dataframe(service, df, DRIVE_FILE_NAME, file_id, DRIVE_FOLDER_ID)
        await update.message.reply_text(
            f'✅ Venda registrada!\n\n'
            f'**Sabor:** {sabor.capitalize()}\n'
            f'**Quantidade:** {quantidade}\n'
            f'**Total:** R$ {total_venda:.2f}\n'
            f'**Lucro:** R$ {lucro_venda:.2f}',
            parse_mode='Markdown'
        )
    except (ValueError, IndexError):
        await update.message.reply_text('❌ *Erro!* Formato: `/venda [sabor] [quantidade]`\nEx: /venda carne 5',
                                        parse_mode='Markdown')
    except Exception as e:
        print(
            f"--- ERRO INESPERADO EM registrar_venda ---\n{traceback.format_exc()}\n----------------------------------------")
        await update.message.reply_text(f"🐛 Erro inesperado no servidor: `{e}`")


def main() -> None:
    if not TELEGRAM_TOKEN:
        raise ValueError("ERRO: Variável de ambiente TELEGRAM_TOKEN não configurada.")

    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("venda", registrar_venda))
    application.add_handler(CommandHandler("diario", relatorio_diario))
    application.add_handler(CommandHandler("lucro", relatorio_lucro_periodo))
    application.add_handler(CommandHandler("vendas", enviar_csv))
    print("Bot SUPER ATUALIZADO (v3 com correção de fuso) iniciado e escutando...")
    application.run_polling()


if __name__ == '__main__':
    main()