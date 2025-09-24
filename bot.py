import os
import pandas as pd
from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
)
import pickle
import json
import base64
import io
import traceback
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

# --- CONFIGURAÇÕES ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID", "")
DRIVE_VENDAS_FILE = "vendas_pasteis.csv"
DRIVE_ESTOQUE_FILE = "estoque_diario.csv"
DRIVE_CONSUMO_FILE = "consumo_pessoal.csv"
DRIVE_FECHAMENTOS_FILE = "historico_fechamentos.csv"
PRECO_FIXO_VENDA = 10.00
PRECO_FIXO_CUSTO = 4.50
SABORES_VALIDOS = ['carne', 'frango']
TIMEZONE = 'America/Sao_Paulo'

plt.switch_backend('Agg')
ASK_CARRYOVER = range(1)

# --- FUNÇÕES DO GOOGLE DRIVE ---
# ... (código do get_drive_service, get_file_id, download_dataframe, upload_dataframe sem alterações)
SCOPES = ['https://www.googleapis.com/auth/drive']


def get_drive_service():
    creds = None;
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
                google_token_base_64 = os.environ.get('GOOGLE_TOKEN_BASE_64')
                google_creds_json = os.environ.get('GOOGLE_CREDENTIALS_JSON')
                if google_token_base_64 and google_creds_json:
                    try:
                        decoded_token = base64.b64decode(google_token_base_64)
                        creds = pickle.loads(decoded_token)
                        if not creds.valid and creds.refresh_token:
                            print("Token do Google expirado. Tentando renovar...")
                            creds.refresh(Request())
                            print("Token renovado com sucesso.")
                    except Exception as e:
                        print(f"Erro ao processar credenciais do Google: {e}")
                        raise ValueError(f"Erro ao processar credenciais: {e}")
                else:
                    raise ValueError("Token ou credenciais não encontrados.")
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)
    return build('drive', 'v3', credentials=creds)


def get_file_id(service, file_name, folder_id):
    query = f"name='{file_name}' and trashed=false";
    if folder_id: query += f" and '{folder_id}' in parents"
    response = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
    files = response.get('files', [])
    return files[0]['id'] if files else None


def download_dataframe(service, file_name, file_id, default_cols):
    if not file_id:
        df = pd.DataFrame(columns=default_cols)
        if default_cols:
            df[default_cols[0]] = pd.to_datetime(df[default_cols[0]], utc=True)
        return df
    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done: status, done = downloader.next_chunk()
    fh.seek(0)
    try:
        df = pd.read_csv(fh)
        if df.empty:
            df = pd.DataFrame(columns=default_cols)
            if default_cols: df[default_cols[0]] = pd.to_datetime(df[default_cols[0]], utc=True)
            return df
        df[df.columns[0]] = pd.to_datetime(df[df.columns[0]], utc=True)
        if file_name == DRIVE_VENDAS_FILE and 'lucro_venda' not in df.columns:
            df['custo_unidade'] = PRECO_FIXO_CUSTO
            df['lucro_venda'] = df['total_venda'] - (df['quantidade'] * PRECO_FIXO_CUSTO)
        return df
    except (pd.errors.EmptyDataError, KeyError, IndexError):
        df = pd.DataFrame(columns=default_cols)
        if default_cols:
            df[default_cols[0]] = pd.to_datetime(df[default_cols[0]], utc=True)
        return df


def upload_dataframe(service, df, file_name, file_id, folder_id):
    csv_bytes = df.to_csv(index=False).encode('utf-8')
    fh = io.BytesIO(csv_bytes)
    media = MediaIoBaseUpload(fh, mimetype='text/csv', resumable=True)
    file_metadata = {'name': file_name}
    if folder_id and not file_id: file_metadata['parents'] = [folder_id]
    if file_id:
        service.files().update(fileId=file_id, media_body=media).execute()
    else:
        service.files().create(body=file_metadata, media_body=media, fields='id').execute()


# --- LÓGICA DE RELATÓRIO REUTILIZÁVEL ---
def gerar_dados_relatorio_diario(data_filtro):
    # (Esta função permanece a mesma da versão anterior)
    service = get_drive_service()
    vendas_fid = get_file_id(service, DRIVE_VENDAS_FILE, DRIVE_FOLDER_ID)
    df_vendas = download_dataframe(service, DRIVE_VENDAS_FILE, vendas_fid,
                                   ['data_hora', 'sabor', 'quantidade', 'preco_unidade', 'custo_unidade', 'total_venda',
                                    'lucro_venda'])
    df_vendas_dia = df_vendas[df_vendas['data_hora'].dt.tz_convert(TIMEZONE).dt.date == data_filtro]
    estoque_fid = get_file_id(service, DRIVE_ESTOQUE_FILE, DRIVE_FOLDER_ID)
    df_estoque = download_dataframe(service, DRIVE_ESTOQUE_FILE, estoque_fid, ['data', 'sabor', 'quantidade_inicial'])
    df_estoque_dia = df_estoque[df_estoque['data'].dt.date == data_filtro]
    consumo_fid = get_file_id(service, DRIVE_CONSUMO_FILE, DRIVE_FOLDER_ID)
    colunas_consumo = ['data_hora', 'sabor', 'quantidade', 'custo_total']
    df_consumo = download_dataframe(service, DRIVE_CONSUMO_FILE, consumo_fid, colunas_consumo)
    df_consumo_dia = df_consumo[df_consumo['data_hora'].dt.tz_convert(TIMEZONE).dt.date == data_filtro]
    titulo_relatorio = f"📊 *Dashboard do Dia {data_filtro.strftime('%d/%m/%Y')}*"
    relatorio_texto = f"{titulo_relatorio}\n\n*Resumo Financeiro (das Vendas)*\n"
    faturamento_bruto = 0
    lucro_liquido_margem = 0
    if not df_vendas_dia.empty:
        faturamento_bruto = df_vendas_dia['total_venda'].sum()
        lucro_liquido_margem = df_vendas_dia['lucro_venda'].sum()
        relatorio_texto += (f"  - Pastéis Vendidos: *{int(df_vendas_dia['quantidade'].sum())}*\n"
                            f"  - Faturamento Bruto: *R$ {faturamento_bruto:.2f}*\n"
                            f"  - Lucro (Margem): *R$ {lucro_liquido_margem:.2f}*")
    else:
        relatorio_texto += "_Nenhuma venda registrada neste dia._"
    relatorio_texto += "\n\n*Gestão de Estoque*\n"
    if not df_estoque_dia.empty:
        for sabor in SABORES_VALIDOS:
            inicial = df_estoque_dia[df_estoque_dia['sabor'] == sabor]['quantidade_inicial'].sum()
            vendido = df_vendas_dia[df_vendas_dia['sabor'] == sabor]['quantidade'].sum()
            consumido = df_consumo_dia[df_consumo_dia['sabor'] == sabor]['quantidade'].sum()
            sobra = inicial - vendido - consumido
            relatorio_texto += (
                f"  - `{sabor.capitalize()}`: Ini: {int(inicial)}, Ven: {int(vendido)}, Con: {int(consumido)} ➜ Sobra: *{int(sobra)}*\n")
        relatorio_texto += "\n---\n\n*Resultado Final do Dia*\n"
        custo_inicial_total = df_estoque_dia['quantidade_inicial'].sum() * PRECO_FIXO_CUSTO
        custo_consumo_pessoal = df_consumo_dia['custo_total'].sum()
        resultado_do_dia = lucro_liquido_margem - custo_consumo_pessoal
        relatorio_texto += f"  - Investimento em Estoque: *R$ {custo_inicial_total:.2f}*\n"
        relatorio_texto += f"  - Faturamento das Vendas: *R$ {faturamento_bruto:.2f}*\n"
        relatorio_texto += f"  - Custo do Consumo Pessoal: *R$ {custo_consumo_pessoal:.2f}*\n"
        if resultado_do_dia >= 0:
            relatorio_texto += f"  - Resultado: *🚀 Lucro de R$ {resultado_do_dia:.2f}*"
        else:
            relatorio_texto += f"  - Resultado: *📉 Prejuízo de R$ {-resultado_do_dia:.2f}*"
    else:
        relatorio_texto += "_Nenhum estoque inicial definido para este dia._"

    sobras_dict = {sabor: int(df_estoque_dia[df_estoque_dia['sabor'] == sabor]['quantidade_inicial'].sum() - \
                              df_vendas_dia[df_vendas_dia['sabor'] == sabor]['quantidade'].sum() - \
                              df_consumo_dia[df_consumo_dia['sabor'] == sabor]['quantidade'].sum()) for sabor in
                   SABORES_VALIDOS}

    return {
        "texto": relatorio_texto,
        "data": data_filtro.strftime('%Y-%m-%d'),
        "pasteis_vendidos": int(df_vendas_dia['quantidade'].sum()),
        "faturamento_bruto": faturamento_bruto,
        "lucro_margem": lucro_liquido_margem,
        "custo_investimento": custo_inicial_total,
        "custo_consumo": custo_consumo_pessoal,
        "resultado_final": resultado_do_dia,
        "sobras": json.dumps(sobras_dict)
    }


async def enviar_relatorio_automatico(context: ContextTypes.DEFAULT_TYPE) -> None:
    # (função sem alterações)
    if not TELEGRAM_CHAT_ID:
        print("TELEGRAM_CHAT_ID não definido. Relatório automático cancelado.")
        return
    print(f"Executando relatório automático para o chat {TELEGRAM_CHAT_ID}...")
    data_hoje = pd.Timestamp.now(tz=TIMEZONE).date()
    dados = gerar_dados_relatorio_diario(data_hoje)
    await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=dados['texto'], parse_mode='Markdown')


# --- DEFINIÇÃO DOS COMANDOS ---
# ... (As funções start, registrar_usuario, definir_estoque, etc., permanecem as mesmas)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_name = update.effective_user.first_name
    help_text = (
        f"Olá, {user_name}! Bem-vindo ao seu assistente de gestão v11!\n\n"
        "**COMANDO DE FIM DE EXPEDIENTE**\n"
        "*/fechamento*\n"
        "_Gera o relatório final, salva em CSV e pergunta sobre as sobras._\n\n"
        "**GESTÃO DIÁRIA**\n"
        "*/estoque [sabor] [qtd]...*\n"
        "Define (ou adiciona a) o estoque inicial do dia.\n"
        "*/venda [sabor] [qtd]*\n"
        "Registra uma venda.\n"
        "*/consumo [sabor] [qtd]*\n"
        "Registra um consumo pessoal.\n"
        "*/ver_estoque*\n"
        "Consulta rápida do estoque atual.\n\n"
        "**RELATÓRIOS E ANÁLISE**\n"
        "*/diario*\n"
        "Relatório completo de hoje.\n"
        "*/lucro [dias]*\n"
        "Lucro acumulado nos últimos dias.\n"
        "*/grafico [dias]*\n"
        "Gera um gráfico de desempenho do lucro.\n"
        "*/vendas*\n"
        "Envia o arquivo `.csv` com o histórico de vendas.\n\n"
        "**CONFIGURAÇÃO**\n"
        "*/registrar*\n"
        "Ativa os relatórios automáticos."
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')


async def fechamento_diario(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        hoje = pd.Timestamp.now(tz=TIMEZONE).date()
        await update.message.reply_text(f"🔒 Iniciando fechamento do dia {hoje.strftime('%d/%m/%Y')}...")

        dados_relatorio = gerar_dados_relatorio_diario(hoje)
        context.user_data['dados_fechamento'] = dados_relatorio

        await update.message.reply_text(dados_relatorio['texto'], parse_mode='Markdown')

        sobras = json.loads(dados_relatorio['sobras'])

        if any(v > 0 for v in sobras.values()):
            sobras_texto = "\n".join([f"  - {s.capitalize()}: {int(q)}" for s, q in sobras.items() if q > 0])
            keyboard = [[InlineKeyboardButton("✅ Sim, lançar", callback_data="carryover_yes"),
                         InlineKeyboardButton("❌ Não, descartar", callback_data="carryover_no")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                f"Foram encontradas as seguintes sobras:\n{sobras_texto}\n\nDeseja lançá-las como estoque inicial para amanhã?",
                reply_markup=reply_markup
            )
            return ASK_CARRYOVER
        else:
            await update.message.reply_text("Nenhuma sobra de estoque encontrada. Salvando relatório...")
            service = get_drive_service()
            fechamentos_fid = get_file_id(service, DRIVE_FECHAMENTOS_FILE, DRIVE_FOLDER_ID)
            colunas_fechamento = list(dados_relatorio.keys())[1:]
            df_fechamentos = download_dataframe(service, DRIVE_FECHAMENTOS_FILE, fechamentos_fid, colunas_fechamento)

            novo_fechamento_df = pd.DataFrame([dados_relatorio])
            novo_fechamento_df = novo_fechamento_df.drop(columns=['texto'])

            df_fechamentos = pd.concat([df_fechamentos, novo_fechamento_df], ignore_index=True)
            upload_dataframe(service, df_fechamentos, DRIVE_FECHAMENTOS_FILE, fechamentos_fid, DRIVE_FOLDER_ID)
            await update.message.reply_text("✅ Fechamento concluído e salvo no histórico CSV!")
            return ConversationHandler.END

    except Exception as e:
        await update.message.reply_text(f"🐛 Erro ao iniciar fechamento: {e}")
        return ConversationHandler.END


async def handle_carryover_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data
    dados_fechamento = context.user_data.get('dados_fechamento', {})

    if not dados_fechamento:
        await query.edit_message_text(text="Erro: dados do fechamento não encontrados. Tente novamente.")
        return ConversationHandler.END

    sobras = json.loads(dados_fechamento.get('sobras', '{}'))
    service = get_drive_service()

    fechamentos_fid = get_file_id(service, DRIVE_FECHAMENTOS_FILE, DRIVE_FOLDER_ID)
    colunas_fechamento = list(dados_fechamento.keys())[1:]
    df_fechamentos = download_dataframe(service, DRIVE_FECHAMENTOS_FILE, fechamentos_fid, colunas_fechamento)

    novo_fechamento_df = pd.DataFrame([dados_fechamento])
    novo_fechamento_df = novo_fechamento_df.drop(columns=['texto'])

    df_fechamentos['data'] = pd.to_datetime(df_fechamentos['data']).dt.strftime('%Y-%m-%d')
    df_fechamentos = df_fechamentos[~(df_fechamentos['data'] == dados_fechamento['data'])]
    df_fechamentos = pd.concat([df_fechamentos, novo_fechamento_df], ignore_index=True)

    upload_dataframe(service, df_fechamentos, DRIVE_FECHAMENTOS_FILE, fechamentos_fid, DRIVE_FOLDER_ID)

    if choice == "carryover_yes" and sobras:
        hoje = pd.Timestamp.now(tz=TIMEZONE).date()
        amanha_str = (hoje + timedelta(days=1)).strftime('%Y-%m-%d')
        estoque_fid = get_file_id(service, DRIVE_ESTOQUE_FILE, DRIVE_FOLDER_ID)
        df_estoque = download_dataframe(service, DRIVE_ESTOQUE_FILE, estoque_fid,
                                        ['data', 'sabor', 'quantidade_inicial'])
        df_estoque['data'] = pd.to_datetime(df_estoque['data']).dt.strftime('%Y-%m-%d')

        for sabor, quantidade in sobras.items():
            if quantidade > 0:
                df_estoque = df_estoque[~((df_estoque['data'] == amanha_str) & (df_estoque['sabor'] == sabor))]
                novo_estoque = pd.DataFrame([{'data': amanha_str, 'sabor': sabor, 'quantidade_inicial': quantidade}])
                df_estoque = pd.concat([df_estoque, novo_estoque], ignore_index=True)

        upload_dataframe(service, df_estoque, DRIVE_ESTOQUE_FILE, estoque_fid, DRIVE_FOLDER_ID)
        await query.edit_message_text(
            text="✅ Fechamento concluído! O relatório foi salvo em CSV e as sobras foram lançadas para amanhã.")
    else:
        await query.edit_message_text(
            text="✅ Fechamento concluído! O relatório foi salvo em CSV e as sobras foram descartadas.")

    context.user_data.clear()
    return ConversationHandler.END


# ----- FUNÇÃO ADICIONADA -----
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancela a operação atual e finaliza a conversa."""
    # Verifica se a mensagem original veio de um comando ou de um botão
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text="Operação cancelada.")
    else:
        await update.message.reply_text("Nenhuma operação em andamento para cancelar.")

    context.user_data.clear()
    return ConversationHandler.END


# ... (outras funções de comando como registrar_usuario, definir_estoque, etc. continuam aqui)
async def registrar_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"✅ Chat registrado.\n\n"
        f"Para relatórios automáticos, adicione a variável `TELEGRAM_CHAT_ID` no Railway com este valor:\n`{chat_id}`"
    )


async def definir_estoque(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not context.args or len(context.args) % 2 != 0:
            await update.message.reply_text("❌ Erro! Formato: `/estoque [sabor1] [qtd1]...`\nEx: `/estoque carne 20`")
            return
        hoje_str = pd.Timestamp.now(tz=TIMEZONE).strftime('%Y-%m-%d')
        await update.message.reply_text("Atualizando estoque do dia...")
        service = get_drive_service()
        estoque_fid = get_file_id(service, DRIVE_ESTOQUE_FILE, DRIVE_FOLDER_ID)
        df_estoque = download_dataframe(service, DRIVE_ESTOQUE_FILE, estoque_fid,
                                        ['data', 'sabor', 'quantidade_inicial'])
        df_estoque['data'] = pd.to_datetime(df_estoque['data']).dt.strftime('%Y-%m-%d')
        resumo_estoque = []
        for i in range(0, len(context.args), 2):
            sabor = context.args[i].lower()
            quantidade = int(context.args[i + 1])
            if sabor not in SABORES_VALIDOS:
                await update.message.reply_text(f"Sabor '{sabor}' inválido. Ignorando.")
                continue
            df_estoque = df_estoque[~((df_estoque['data'] == hoje_str) & (df_estoque['sabor'] == sabor))]
            novo_estoque = pd.DataFrame([{'data': hoje_str, 'sabor': sabor, 'quantidade_inicial': quantidade}])
            df_estoque = pd.concat([df_estoque, novo_estoque], ignore_index=True)
            resumo_estoque.append(f"  - {sabor.capitalize()}: {quantidade} unidades")
        upload_dataframe(service, df_estoque, DRIVE_ESTOQUE_FILE, estoque_fid, DRIVE_FOLDER_ID)
        mensagem_resumo = "✅ Estoque inicial de hoje definido:\n" + "\n".join(resumo_estoque)
        await update.message.reply_text(mensagem_resumo)
    except Exception as e:
        await update.message.reply_text(f"🐛 Erro inesperado ao definir estoque: {e}")


async def registrar_venda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if len(context.args) != 2: raise ValueError("Formato incorreto")
        sabor = context.args[0].lower()
        quantidade_venda = int(context.args[1])
        if sabor not in SABORES_VALIDOS:
            sabores_str = ", ".join(SABORES_VALIDOS)
            await update.message.reply_text(f"❌ Sabor inválido. Use: *{sabores_str}*.", parse_mode='Markdown')
            return
        hoje = pd.Timestamp.now(tz=TIMEZONE).date()
        service = get_drive_service()
        estoque_fid = get_file_id(service, DRIVE_ESTOQUE_FILE, DRIVE_FOLDER_ID)
        df_estoque = download_dataframe(service, DRIVE_ESTOQUE_FILE, estoque_fid,
                                        ['data', 'sabor', 'quantidade_inicial'])
        estoque_hoje = df_estoque[df_estoque['data'].dt.date == hoje]
        if estoque_hoje.empty:
            await update.message.reply_text("⚠️ Atenção! Estoque de hoje não definido. Use `/estoque`.")
            return
        estoque_sabor = estoque_hoje[estoque_hoje['sabor'] == sabor]
        if estoque_sabor.empty:
            await update.message.reply_text(f"⚠️ Atenção! Não há estoque inicial para '{sabor.capitalize()}' hoje.")
            return
        estoque_inicial = estoque_sabor['quantidade_inicial'].iloc[0]
        vendas_fid = get_file_id(service, DRIVE_VENDAS_FILE, DRIVE_FOLDER_ID)
        df_vendas = download_dataframe(service, DRIVE_VENDAS_FILE, vendas_fid, ['data_hora', 'sabor', 'quantidade'])
        vendas_hoje_sabor = df_vendas[
            (df_vendas['data_hora'].dt.tz_convert(TIMEZONE).dt.date == hoje) & (df_vendas['sabor'] == sabor)]
        ja_vendido = vendas_hoje_sabor['quantidade'].sum()
        consumo_fid = get_file_id(service, DRIVE_CONSUMO_FILE, DRIVE_FOLDER_ID)
        colunas_consumo = ['data_hora', 'sabor', 'quantidade', 'custo_total']
        df_consumo = download_dataframe(service, DRIVE_CONSUMO_FILE, consumo_fid, colunas_consumo)
        consumo_hoje_sabor = df_consumo[
            (df_consumo['data_hora'].dt.tz_convert(TIMEZONE).dt.date == hoje) & (df_consumo['sabor'] == sabor)]
        ja_consumido = consumo_hoje_sabor['quantidade'].sum()
        estoque_atual = estoque_inicial - ja_vendido - ja_consumido
        if quantidade_venda > estoque_atual:
            await update.message.reply_text(f"❌ Venda não registrada! Estoque insuficiente: *{int(estoque_atual)}*.",
                                            parse_mode='Markdown')
            return
        preco_unidade, custo_unidade = PRECO_FIXO_VENDA, PRECO_FIXO_CUSTO
        total_venda = quantidade_venda * preco_unidade
        lucro_venda = total_venda - (quantidade_venda * custo_unidade)
        nova_venda = pd.DataFrame(
            [{'data_hora': pd.to_datetime('now', utc=True), 'sabor': sabor, 'quantidade': quantidade_venda,
              'preco_unidade': preco_unidade, 'custo_unidade': custo_unidade, 'total_venda': total_venda,
              'lucro_venda': lucro_venda}])
        df_vendas = pd.concat([df_vendas, nova_venda], ignore_index=True)
        upload_dataframe(service, df_vendas, DRIVE_VENDAS_FILE, vendas_fid, DRIVE_FOLDER_ID)
        await update.message.reply_text(
            f'✅ Venda registrada! Estoque restante de {sabor.capitalize()}: {int(estoque_atual - quantidade_venda)}')
    except (ValueError, IndexError):
        await update.message.reply_text('❌ *Erro!* Formato: `/venda [sabor] [quantidade]`', parse_mode='Markdown')
    except Exception as e:
        print(
            f"--- ERRO INESPERADO EM registrar_venda ---\n{traceback.format_exc()}\n----------------------------------------")
        await update.message.reply_text(f"🐛 Erro inesperado no servidor: `{e}`")


async def consumo_pessoal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if len(context.args) != 2: raise ValueError("Formato incorreto")
        sabor = context.args[0].lower()
        quantidade_consumo = int(context.args[1])
        if sabor not in SABORES_VALIDOS:
            await update.message.reply_text(f"❌ Sabor inválido: *{sabor}*.", parse_mode='Markdown')
            return
        hoje = pd.Timestamp.now(tz=TIMEZONE).date()
        service = get_drive_service()
        estoque_fid = get_file_id(service, DRIVE_ESTOQUE_FILE, DRIVE_FOLDER_ID)
        df_estoque = download_dataframe(service, DRIVE_ESTOQUE_FILE, estoque_fid,
                                        ['data', 'sabor', 'quantidade_inicial'])
        estoque_hoje = df_estoque[df_estoque['data'].dt.date == hoje]
        if estoque_hoje.empty:
            await update.message.reply_text("⚠️ Atenção! Estoque de hoje não definido. Use `/estoque`.")
            return
        estoque_sabor = estoque_hoje[estoque_hoje['sabor'] == sabor]
        if estoque_sabor.empty:
            await update.message.reply_text(f"⚠️ Atenção! Não há estoque inicial para '{sabor.capitalize()}' hoje.")
            return
        estoque_inicial = estoque_sabor['quantidade_inicial'].iloc[0]
        vendas_fid = get_file_id(service, DRIVE_VENDAS_FILE, DRIVE_FOLDER_ID)
        df_vendas = download_dataframe(service, DRIVE_VENDAS_FILE, vendas_fid, ['data_hora', 'sabor', 'quantidade'])
        vendas_hoje_sabor = df_vendas[
            (df_vendas['data_hora'].dt.tz_convert(TIMEZONE).dt.date == hoje) & (df_vendas['sabor'] == sabor)]
        ja_vendido = vendas_hoje_sabor['quantidade'].sum()
        consumo_fid = get_file_id(service, DRIVE_CONSUMO_FILE, DRIVE_FOLDER_ID)
        colunas_consumo = ['data_hora', 'sabor', 'quantidade', 'custo_total']
        df_consumo = download_dataframe(service, DRIVE_CONSUMO_FILE, consumo_fid, colunas_consumo)
        consumo_hoje_sabor = df_consumo[
            (df_consumo['data_hora'].dt.tz_convert(TIMEZONE).dt.date == hoje) & (df_consumo['sabor'] == sabor)]
        ja_consumido = consumo_hoje_sabor['quantidade'].sum()
        estoque_atual = estoque_inicial - ja_vendido - ja_consumido
        if quantidade_consumo > estoque_atual:
            await update.message.reply_text(f"❌ Consumo não registrado! Estoque insuficiente: *{int(estoque_atual)}*.",
                                            parse_mode='Markdown')
            return
        novo_consumo = pd.DataFrame(
            [{'data_hora': pd.to_datetime('now', utc=True), 'sabor': sabor, 'quantidade': quantidade_consumo,
              'custo_total': quantidade_consumo * PRECO_FIXO_CUSTO}])
        df_consumo = pd.concat([df_consumo, novo_consumo], ignore_index=True)
        upload_dataframe(service, df_consumo, DRIVE_CONSUMO_FILE, consumo_fid, DRIVE_FOLDER_ID)
        await update.message.reply_text(
            f'✅ Consumo pessoal registrado! Estoque restante de {sabor.capitalize()}: {int(estoque_atual - quantidade_consumo)}')
    except (ValueError, IndexError):
        await update.message.reply_text('❌ *Erro!* Formato: `/consumo [sabor] [quantidade]`', parse_mode='Markdown')
    except Exception as e:
        print(
            f"--- ERRO INESPERADO EM consumo_pessoal ---\n{traceback.format_exc()}\n----------------------------------------")
        await update.message.reply_text(f"🐛 Erro inesperado no servidor: `{e}`")


async def relatorio_diario_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if context.args:
            data_filtro = pd.to_datetime(context.args[0]).date()
        else:
            data_filtro = pd.Timestamp.now(tz=TIMEZONE).date()
        dados = gerar_dados_relatorio_diario(data_filtro)
        await update.message.reply_text(dados['texto'], parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"🐛 Erro ao gerar relatório: {e}")


async def ver_estoque_atual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        hoje = pd.Timestamp.now(tz=TIMEZONE).date()
        service = get_drive_service()
        estoque_fid = get_file_id(service, DRIVE_ESTOQUE_FILE, DRIVE_FOLDER_ID)
        df_estoque = download_dataframe(service, DRIVE_ESTOQUE_FILE, estoque_fid,
                                        ['data', 'sabor', 'quantidade_inicial'])
        estoque_hoje = df_estoque[df_estoque['data'].dt.date == hoje]
        if estoque_hoje.empty:
            await update.message.reply_text("Estoque de hoje ainda não definido. Use `/estoque`.")
            return
        vendas_fid = get_file_id(service, DRIVE_VENDAS_FILE, DRIVE_FOLDER_ID)
        df_vendas = download_dataframe(service, DRIVE_VENDAS_FILE, vendas_fid, ['data_hora', 'sabor', 'quantidade'])
        vendas_hoje = df_vendas[df_vendas['data_hora'].dt.tz_convert(TIMEZONE).dt.date == hoje]
        consumo_fid = get_file_id(service, DRIVE_CONSUMO_FILE, DRIVE_FOLDER_ID)
        df_consumo = download_dataframe(service, DRIVE_CONSUMO_FILE, consumo_fid,
                                        ['data_hora', 'sabor', 'quantidade', 'custo_total'])
        consumo_hoje = df_consumo[df_consumo['data_hora'].dt.tz_convert(TIMEZONE).dt.date == hoje]
        relatorio_texto = "📦 *Estoque Atual*\n\n"
        for index, row in estoque_hoje.iterrows():
            sabor = row['sabor']
            inicial = row['quantidade_inicial']
            vendido = vendas_hoje[vendas_hoje['sabor'] == sabor]['quantidade'].sum()
            consumido = consumo_hoje[consumo_hoje['sabor'] == sabor]['quantidade'].sum()
            sobra = inicial - vendido - consumido
            relatorio_texto += f"- {sabor.capitalize()}: *{int(sobra)}* unidades\n"
        await update.message.reply_text(relatorio_texto, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"🐛 Erro ao verificar estoque: {e}")


async def gerar_grafico(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        plt.style.use('seaborn-v0_8-whitegrid')
        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text("❌ Erro! Formato: `/grafico [dias]`\nExemplo: `/grafico 7`")
            return
        dias = int(context.args[0])
        await update.message.reply_text(f"Gerando gráfico de lucro dos últimos {dias} dias...")
        service = get_drive_service()
        vendas_fid = get_file_id(service, DRIVE_VENDAS_FILE, DRIVE_FOLDER_ID)
        df_vendas = download_dataframe(service, DRIVE_VENDAS_FILE, vendas_fid,
                                       ['data_hora', 'sabor', 'quantidade', 'preco_unidade', 'custo_unidade',
                                        'total_venda', 'lucro_venda'])
        if df_vendas.empty:
            await update.message.reply_text("Nenhuma venda encontrada para gerar o gráfico.")
            return
        hoje = pd.Timestamp.now(tz=TIMEZONE).date()
        data_inicio = hoje - timedelta(days=dias - 1)
        df_periodo = df_vendas[df_vendas['data_hora'].dt.tz_convert(TIMEZONE).dt.date >= data_inicio]
        if df_periodo.empty:
            await update.message.reply_text(f"Nenhuma venda nos últimos {dias} dias.")
            return
        lucro_por_dia = df_periodo.groupby(df_periodo['data_hora'].dt.tz_convert(TIMEZONE).dt.date)['lucro_venda'].sum()
        fig, ax = plt.subplots(figsize=(12, 7))
        bars = ax.bar(lucro_por_dia.index, lucro_por_dia.values, color='#4A90E2', label='Lucro Diário')
        for bar in bars:
            yval = bar.get_height()
            ax.text(bar.get_x() + bar.get_width() / 2.0, yval, f'R${yval:.2f}', va='bottom' if yval >= 0 else 'top',
                    ha='center')
        media_lucro = lucro_por_dia.mean()
        ax.axhline(media_lucro, color='red', linestyle='--', linewidth=2, label=f'Média: R$ {media_lucro:.2f}')
        ax.set_title(f'Lucro Líquido por Dia (Últimos {dias} Dias)', fontsize=16, pad=20)
        ax.set_ylabel('Lucro (R$)');
        ax.set_xlabel('Data', fontsize=12)
        ax.tick_params(axis='x', rotation=45)
        ax.grid(axis='y', linestyle='--', alpha=0.7)
        ax.spines['top'].set_visible(False);
        ax.spines['right'].set_visible(False)
        ax.legend()
        ax.set_xticklabels([d.strftime('%d/%m') for d in lucro_por_dia.index])
        ax.set_ylim(top=ax.get_ylim()[1] * 1.15)
        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format='png');
        buf.seek(0);
        plt.close(fig)
        total_lucro = lucro_por_dia.sum()
        caption = (f"📈 *Relatório Gráfico de Lucro*\n\n"
                   f"▫️ Período Analisado: *Últimos {dias} dias*\n"
                   f"▫️ Lucro Total no Período: *R$ {total_lucro:.2f}*\n"
                   f"▫️ Média de Lucro Diário: *R$ {media_lucro:.2f}*")
        await update.message.reply_photo(photo=buf, caption=caption, parse_mode='Markdown')
    except Exception as e:
        print(
            f"--- ERRO INESPERADO EM gerar_grafico ---\n{traceback.format_exc()}\n----------------------------------------")
        await update.message.reply_text(f"🐛 Erro ao gerar gráfico: {e}")


async def relatorio_lucro_periodo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text("❌ Erro! Use o formato: `/lucro [dias]`\nExemplo: `/lucro 7`")
            return
        dias = int(context.args[0])
        hoje = pd.Timestamp.now(tz=TIMEZONE).date()
        data_inicio = hoje - timedelta(days=dias - 1)
        await update.message.reply_text(f"Gerando relatório de lucro dos últimos {dias} dias...")
        service = get_drive_service()
        df = download_dataframe(service, DRIVE_VENDAS_FILE, get_file_id(service, DRIVE_VENDAS_FILE, DRIVE_FOLDER_ID),
                                ['data_hora', 'sabor', 'quantidade', 'preco_unidade', 'custo_unidade', 'total_venda',
                                 'lucro_venda'])
        if df.empty:
            await update.message.reply_text("Nenhuma venda encontrada para gerar relatórios.")
            return
        df_periodo = df[(df['data_hora'].dt.tz_convert(TIMEZONE).dt.date >= data_inicio) & (
                    df['data_hora'].dt.tz_convert(TIMEZONE).dt.date <= hoje)]
        if df_periodo.empty:
            await update.message.reply_text(f"Nenhuma venda registrada nos últimos {dias} dias.")
            return
        lucro_total_periodo = df_periodo['lucro_venda'].sum()
        relatorio_texto = (f"📈 *Lucro (Margem) dos Últimos {dias} Dias*\n"
                           f"_{data_inicio.strftime('%d/%m/%Y')} a {hoje.strftime('%d/%m/%Y')}_\n\n"
                           f"🚀 Lucro Líquido Total: *R$ {lucro_total_periodo:.2f}*")
        await update.message.reply_text(relatorio_texto, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"Erro ao gerar relatório de período: {e}")


async def enviar_csv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await update.message.reply_text("Buscando o arquivo de vendas no Drive...")
        service = get_drive_service()
        vendas_fid = get_file_id(service, DRIVE_VENDAS_FILE, DRIVE_FOLDER_ID)
        if not vendas_fid:
            await update.message.reply_text("Nenhum arquivo de vendas encontrado.")
            return
        request = service.files().get_media(fileId=vendas_fid)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        fh.seek(0)
        await update.message.reply_document(document=InputFile(fh, filename=DRIVE_VENDAS_FILE),
                                            caption="Aqui está o seu relatório de vendas completo.")
    except Exception as e:
        await update.message.reply_text(f"Ocorreu um erro ao enviar o arquivo: {e}")


async def post_init(application: Application) -> None:
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    scheduler.add_job(enviar_relatorio_automatico, 'cron', hour=19, minute=30, args=[application])
    scheduler.start()
    print("Agendador de tarefas iniciado e configurado para 19:30.")


def main() -> None:
    if not TELEGRAM_TOKEN:
        raise ValueError("ERRO: Variável de ambiente TELEGRAM_TOKEN não configurada.")

    application = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("fechamento", fechamento_diario)],
        states={
            ASK_CARRYOVER: [CallbackQueryHandler(handle_carryover_choice)]
        },
        fallbacks=[CommandHandler("cancelar", cancel)],
    )
    application.add_handler(conv_handler)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("registrar", registrar_usuario))
    application.add_handler(CommandHandler("estoque", definir_estoque))
    application.add_handler(CommandHandler("venda", registrar_venda))
    application.add_handler(CommandHandler("consumo", consumo_pessoal))
    application.add_handler(CommandHandler("diario", relatorio_diario_handler))
    application.add_handler(CommandHandler("lucro", relatorio_lucro_periodo))
    application.add_handler(CommandHandler("vendas", enviar_csv))
    application.add_handler(CommandHandler("ver_estoque", ver_estoque_atual))
    application.add_handler(CommandHandler("grafico", gerar_grafico))

    print("Bot com Fechamento Interativo (v11) iniciado...")
    application.run_polling()


if __name__ == '__main__':
    main()