import os
import pandas as pd
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import pickle
import json
import base64
import io

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

# --- CONFIGURAÇÕES LENDO DE VARIÁVEIS DE AMBIENTE ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
DRIVE_FILE_NAME = "vendas_pasteis.csv"
# A ID da pasta é opcional, será lida da variável de ambiente se existir
DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID", "")

# --- AUTENTICAÇÃO E FUNÇÕES DO GOOGLE DRIVE (MODIFICADO PARA RAILWAY) ---

SCOPES = ['https://www.googleapis.com/auth/drive']


def get_drive_service():
    """Autentica e retorna um objeto de serviço para o Google Drive."""
    creds = None

    # No Railway, o token.pickle será recriado em memória a cada deploy
    # a partir da variável de ambiente.
    google_token_base64 = os.environ.get('GOOGLE_TOKEN_BASE64')
    if google_token_base64:
        try:
            decoded_token = base64.b64decode(google_token_base64)
            creds = pickle.loads(decoded_token)
        except (pickle.UnpicklingError, base64.binascii.Error) as e:
            raise ValueError(f"Erro ao decodificar GOOGLE_TOKEN_BASE64: {e}")

    # Se o token não existir ou for inválido, tenta usar as credenciais JSON.
    # Este fluxo é mais um fallback, o ideal é que o token exista.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            google_creds_json = os.environ.get('GOOGLE_CREDENTIALS_JSON')
            if not google_creds_json:
                raise ValueError(
                    "ERRO: Variáveis de ambiente GOOGLE_TOKEN_BASE64 ou GOOGLE_CREDENTIALS_JSON não encontradas.")

            # Recria o token a partir do JSON (exigiria re-autenticação se o token expirasse)
            creds_info = json.loads(google_creds_json)
            # Este fluxo NÃO DEVE ser usado para o primeiro login no Railway
            # pois precisa de interação do usuário. O token.pickle (em base64) é essencial.
            flow = InstalledAppFlow.from_client_config(creds_info, SCOPES)
            # A linha abaixo é apenas ilustrativa, não funcionará no servidor.
            # creds = flow.run_local_server(port=0)
            raise ValueError(
                "Token inválido ou expirado. Gere um novo 'token.pickle' localmente e atualize a variável GOOGLE_TOKEN_BASE64.")

    return build('drive', 'v3', credentials=creds)


def get_file_id(service, file_name, folder_id):
    """Procura por um arquivo no Drive e retorna seu ID."""
    query = f"name='{file_name}' and trashed=false"
    if folder_id:
        query += f" and '{folder_id}' in parents"
    response = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
    files = response.get('files', [])
    return files[0]['id'] if files else None


def download_dataframe(service, file_id):
    """Baixa o arquivo do Drive e o carrega em um DataFrame pandas."""
    if not file_id:
        return pd.DataFrame(columns=['data_hora', 'sabor', 'quantidade', 'preco_unidade', 'total_venda'])
    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while done is False:
        status, done = downloader.next_chunk()
    fh.seek(0)
    try:
        return pd.read_csv(fh)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=['data_hora', 'sabor', 'quantidade', 'preco_unidade', 'total_venda'])


def upload_dataframe(service, df, file_name, file_id, folder_id):
    """Salva o DataFrame como um arquivo CSV no Google Drive usando um buffer em memória."""
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


# --- COMANDOS DO BOT (sem alterações) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_name = update.effective_user.first_name
    await update.message.reply_text(
        f'Olá, {user_name}! Bem-vindo ao seu assistente de vendas de pastéis.\n\n'
        'Use os seguintes comandos:\n'
        '/venda [sabor] [qtd] [preço] - Registra uma nova venda.\n'
        'Ex: /venda carne 2 7.50\n\n'
        '/relatorio - Mostra o resumo das vendas.'
    )


async def registrar_venda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        sabor = context.args[0]
        quantidade = int(context.args[1])
        preco_unidade = float(context.args[2])
        total_venda = quantidade * preco_unidade
        await update.message.reply_text("Registrando venda na nuvem...")
        service = get_drive_service()
        file_id = get_file_id(service, DRIVE_FILE_NAME, DRIVE_FOLDER_ID)
        df = download_dataframe(service, file_id)
        nova_venda = pd.DataFrame([{
            'data_hora': pd.to_datetime('now', utc=True).strftime('%Y-%m-%d %H:%M:%S'),
            'sabor': sabor,
            'quantidade': quantidade,
            'preco_unidade': preco_unidade,
            'total_venda': total_venda
        }])
        df = pd.concat([df, nova_venda], ignore_index=True)
        upload_dataframe(service, df, DRIVE_FILE_NAME, file_id, DRIVE_FOLDER_ID)
        await update.message.reply_text(
            f'✅ Venda registrada com sucesso!\n\n'
            f'Sabor: {sabor}\n'
            f'Quantidade: {quantidade}\n'
            f'Total: R$ {total_venda:.2f}'
        )
    except (IndexError, ValueError):
        await update.message.reply_text(
            '❌ Erro! Use o formato correto:\n'
            '/venda [sabor] [quantidade] [preço_unitário]\n\n'
            'Exemplo: /venda frango 3 8.00'
        )
    except Exception as e:
        print(f"Ocorreu um erro: {e}")
        await update.message.reply_text(f"Ocorreu um erro interno ao registrar a venda. Detalhes: {e}")


async def gerar_relatorio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await update.message.reply_text("Gerando relatório, buscando dados no Drive...")
        service = get_drive_service()
        file_id = get_file_id(service, DRIVE_FILE_NAME, DRIVE_FOLDER_ID)
        if not file_id:
            await update.message.reply_text("Nenhuma venda registrada ainda.")
            return
        df = download_dataframe(service, file_id)
        if df.empty:
            await update.message.reply_text("Nenhuma venda registrada ainda.")
            return
        total_vendido = df['total_venda'].sum()
        quantidade_total_pasteis = df['quantidade'].sum()
        vendas_por_sabor = df.groupby('sabor')['quantidade'].sum().sort_values(ascending=False)
        relatorio_texto = f'📊 *Dashboard de Vendas* 📊\n\n'
        relatorio_texto += f'💰 *Total Bruto Vendido:* R$ {total_vendido:.2f}\n'
        relatorio_texto += f'🥟 *Total de Pastéis Vendidos:* {int(quantidade_total_pasteis)}\n\n'
        relatorio_texto += f'*Sabores mais vendidos:*\n'
        for sabor, qtd in vendas_por_sabor.items():
            relatorio_texto += f'- {sabor.capitalize()}: {int(qtd)} unidades\n'
        relatorio_texto += '\n\n_(Relatório de faturamento bruto)_'
        await update.message.reply_text(relatorio_texto, parse_mode='Markdown')
    except Exception as e:
        print(f"Ocorreu um erro: {e}")
        await update.message.reply_text(f"Ocorreu um erro interno ao gerar o relatório. Detalhes: {e}")


def main() -> None:
    """Inicia o bot."""
    if not TELEGRAM_TOKEN:
        raise ValueError("ERRO: Variável de ambiente TELEGRAM_TOKEN não configurada.")

    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("venda", registrar_venda))
    application.add_handler(CommandHandler("relatorio", gerar_relatorio))
    print("Bot iniciado na nuvem e escutando...")
    application.run_polling()


if __name__ == '__main__':
    main()