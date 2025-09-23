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
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

# --- CONFIGURAÇÕES ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")  # NOVO: Para relatórios automáticos
DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID", "")
DRIVE_VENDAS_FILE = "vendas_pasteis.csv"
DRIVE_ESTOQUE_FILE = "estoque_diario.csv"
PRECO_FIXO_VENDA = 10.00
PRECO_FIXO_CUSTO = 4.50
SABORES_VALIDOS = ['carne', 'frango']
TIMEZONE = 'America/Sao_Paulo'

# Configura o Matplotlib para rodar no servidor sem interface gráfica
plt.switch_backend('Agg')

# --- FUNÇÕES DO GOOGLE DRIVE (sem alterações) ---
# ... (As funções get_drive_service, get_file_id, download_dataframe, upload_dataframe permanecem as mesmas da versão anterior)
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
    query = f"name='{file_name}' and trashed=false";
    if folder_id: query += f" and '{folder_id}' in parents"
    response = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
    files = response.get('files', [])
    return files[0]['id'] if files else None


def download_dataframe(service, file_name, file_id, default_cols):
    if not file_id: return pd.DataFrame(columns=default_cols)
    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done: status, done = downloader.next_chunk()
    fh.seek(0)
    try:
        df = pd.read_csv(fh)
        df[df.columns[0]] = pd.to_datetime(df[df.columns[0]])
        if file_name == DRIVE_VENDAS_FILE and 'lucro_venda' not in df.columns:
            df['custo_unidade'] = PRECO_FIXO_CUSTO
            df['lucro_venda'] = df['total_venda'] - (df['quantidade'] * PRECO_FIXO_CUSTO)
        return df
    except (pd.errors.EmptyDataError, KeyError, IndexError):
        return pd.DataFrame(columns=default_cols)


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

def gerar_texto_relatorio_diario(data_filtro):
    """Função que busca dados e gera o texto do relatório diário."""
    service = get_drive_service()

    # Busca dados de vendas
    vendas_fid = get_file_id(service, DRIVE_VENDAS_FILE, DRIVE_FOLDER_ID)
    colunas_vendas = ['data_hora', 'sabor', 'quantidade', 'preco_unidade', 'custo_unidade', 'total_venda',
                      'lucro_venda']
    df_vendas = download_dataframe(service, DRIVE_VENDAS_FILE, vendas_fid, colunas_vendas)
    df_vendas_dia = df_vendas[df_vendas['data_hora'].dt.tz_convert(TIMEZONE).dt.date == data_filtro]

    # Busca dados de estoque
    estoque_fid = get_file_id(service, DRIVE_ESTOQUE_FILE, DRIVE_FOLDER_ID)
    df_estoque = download_dataframe(service, DRIVE_ESTOQUE_FILE, estoque_fid, ['data', 'sabor', 'quantidade_inicial'])
    df_estoque_dia = df_estoque[df_estoque['data'].dt.date == data_filtro]

    titulo_relatorio = f"📊 *Dashboard do Dia {data_filtro.strftime('%d/%m/%Y')}*"
    relatorio_texto = f"{titulo_relatorio}\n\n*Resumo Financeiro (das Vendas)*\n"
    faturamento_bruto = 0
    if not df_vendas_dia.empty:
        total_pasteis = df_vendas_dia['quantidade'].sum()
        faturamento_bruto = df_vendas_dia['total_venda'].sum()
        lucro_liquido_margem = df_vendas_dia['lucro_venda'].sum()
        relatorio_texto += (f"  - Pastéis Vendidos: *{int(total_pasteis)}*\n"
                            f"  - Faturamento Bruto: *R$ {faturamento_bruto:.2f}*\n"
                            f"  - Lucro (Margem): *R$ {lucro_liquido_margem:.2f}*")
    else:
        relatorio_texto += "_Nenhuma venda registrada neste dia._"

    relatorio_texto += "\n\n*Gestão de Estoque*\n"
    if not df_estoque_dia.empty:
        for index, row in df_estoque_dia.iterrows():
            sabor = row['sabor']
            inicial = row['quantidade_inicial']
            vendido = df_vendas_dia[df_vendas_dia['sabor'] == sabor]['quantidade'].sum()
            sobra = inicial - vendido
            relatorio_texto += (
                f"  - `{sabor.capitalize()}`: Começou com {int(inicial)}, vendeu {int(vendido)}, sobrou *{int(sobra)}*\n")

        relatorio_texto += "\n---\n\n*Ponto de Equilíbrio do Dia*\n"
        custo_inicial_total = df_estoque_dia['quantidade_inicial'].sum() * PRECO_FIXO_CUSTO
        resultado_do_dia = faturamento_bruto - custo_inicial_total
        relatorio_texto += f"  - Investimento em Estoque: *R$ {custo_inicial_total:.2f}*\n"
        relatorio_texto += f"  - Faturamento das Vendas: *R$ {faturamento_bruto:.2f}*\n"
        if resultado_do_dia >= 0:
            relatorio_texto += f"  - Resultado Final: *🚀 Lucro de R$ {resultado_do_dia:.2f}*"
        else:
            relatorio_texto += f"  - Resultado Final: *📉 R$ {resultado_do_dia:.2f}*\n"
            relatorio_texto += f"  _(Faltam R$ {-resultado_do_dia:.2f} para cobrir o investimento)_"
    else:
        relatorio_texto += "_Nenhum estoque inicial definido para este dia._"

    return relatorio_texto


# --- DEFINIÇÃO DOS COMANDOS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        'Olá! Bem-vindo ao seu sistema de gestão v5.0!\n\n'
        '**Novos Comandos:**\n'
        '**/registrar** - _Execute este comando 1 vez para ativar os relatórios automáticos._\n'
        '**/ver_estoque** - _Consulta rápida do estoque atual._\n'
        '**/grafico [dias]** - _Gera um gráfico de lucro. Ex: /grafico 7_\n\n'
        '**Comandos Principais:**\n'
        '**/estoque**, **/venda**, **/diario**, **/lucro**, **/vendas**',
        parse_mode='Markdown'
    )


async def registrar_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"✅ Ótimo! Seu chat foi registrado.\n\n"
        f"Para ativar os relatórios automáticos, adicione esta variável de ambiente no Railway:\n\n"
        f"`TELEGRAM_CHAT_ID`\n\n"
        f"Com este valor:\n`{chat_id}`\n\n"
        f"O bot enviará o relatório diário aqui, todos os dias às 19:30."
    )


async def ver_estoque_atual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        hoje = pd.Timestamp.now(tz=TIMEZONE).date()
        service = get_drive_service()

        # Pega estoque inicial
        estoque_fid = get_file_id(service, DRIVE_ESTOQUE_FILE, DRIVE_FOLDER_ID)
        df_estoque = download_dataframe(service, DRIVE_ESTOQUE_FILE, estoque_fid,
                                        ['data', 'sabor', 'quantidade_inicial'])
        estoque_hoje = df_estoque[df_estoque['data'].dt.date == hoje]

        if estoque_hoje.empty:
            await update.message.reply_text("Estoque de hoje ainda não definido. Use `/estoque`.")
            return

        # Pega vendas de hoje
        vendas_fid = get_file_id(service, DRIVE_VENDAS_FILE, DRIVE_FOLDER_ID)
        colunas_vendas = ['data_hora', 'sabor', 'quantidade', 'preco_unidade', 'custo_unidade', 'total_venda',
                          'lucro_venda']
        df_vendas = download_dataframe(service, DRIVE_VENDAS_FILE, vendas_fid, colunas_vendas)
        vendas_hoje = df_vendas[df_vendas['data_hora'].dt.tz_convert(TIMEZONE).dt.date == hoje]

        relatorio_texto = "📦 *Estoque Atual*\n\n"
        for index, row in estoque_hoje.iterrows():
            sabor = row['sabor']
            inicial = row['quantidade_inicial']
            vendido = vendas_hoje[vendas_hoje['sabor'] == sabor]['quantidade'].sum()
            sobra = inicial - vendido
            relatorio_texto += f"- {sabor.capitalize()}: *{int(sobra)}* unidades\n"

        await update.message.reply_text(relatorio_texto, parse_mode='Markdown')

    except Exception as e:
        await update.message.reply_text(f"🐛 Erro ao verificar estoque: {e}")


async def gerar_grafico(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text("❌ Erro! Formato: `/grafico [dias]`\nExemplo: `/grafico 7`")
            return

        dias = int(context.args[0])
        await update.message.reply_text(f"Gerando gráfico de lucro dos últimos {dias} dias...")

        service = get_drive_service()
        vendas_fid = get_file_id(service, DRIVE_VENDAS_FILE, DRIVE_FOLDER_ID)
        colunas_vendas = ['data_hora', 'sabor', 'quantidade', 'preco_unidade', 'custo_unidade', 'total_venda',
                          'lucro_venda']
        df_vendas = download_dataframe(service, DRIVE_VENDAS_FILE, vendas_fid, colunas_vendas)

        if df_vendas.empty:
            await update.message.reply_text("Nenhuma venda encontrada para gerar o gráfico.")
            return

        # Filtra e agrupa os dados
        hoje = pd.Timestamp.now(tz=TIMEZONE).date()
        data_inicio = hoje - timedelta(days=dias - 1)
        df_periodo = df_vendas[df_vendas['data_hora'].dt.tz_convert(TIMEZONE).dt.date >= data_inicio]

        if df_periodo.empty:
            await update.message.reply_text(f"Nenhuma venda nos últimos {dias} dias.")
            return

        lucro_por_dia = df_periodo.groupby(df_periodo['data_hora'].dt.tz_convert(TIMEZONE).dt.date)['lucro_venda'].sum()

        # Gera o gráfico
        fig, ax = plt.subplots(figsize=(10, 6))
        lucro_por_dia.plot(kind='bar', ax=ax, color='skyblue')

        ax.set_title(f'Lucro Líquido por Dia (Últimos {dias} Dias)', fontsize=16)
        ax.set_ylabel('Lucro (R$)')
        ax.set_xlabel('Data')
        ax.tick_params(axis='x', rotation=45)
        ax.grid(axis='y', linestyle='--', alpha=0.7)

        # Formata o eixo X para mostrar datas de forma legível
        ax.set_xticklabels([d.strftime('%d/%m') for d in lucro_por_dia.index])

        plt.tight_layout()

        # Salva o gráfico em um buffer de memória
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        plt.close(fig)  # Fecha a figura para liberar memória

        await update.message.reply_photo(photo=buf, caption=f"Total lucrado no período: R$ {lucro_por_dia.sum():.2f}")

    except Exception as e:
        await update.message.reply_text(f"🐛 Erro ao gerar gráfico: {e}")


async def relatorio_diario_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler que chama a função de gerar texto e envia a resposta."""
    try:
        if context.args:
            data_filtro = pd.to_datetime(context.args[0]).date()
        else:
            data_filtro = pd.Timestamp.now(tz=TIMEZONE).date()

        texto = gerar_texto_relatorio_diario(data_filtro)
        await update.message.reply_text(texto, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"🐛 Erro ao gerar relatório: {e}")


# --- FUNÇÃO AUTOMÁTICA ---
async def enviar_relatorio_automatico(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Função executada pelo agendador."""
    if not TELEGRAM_CHAT_ID:
        print("TELEGRAM_CHAT_ID não definido. Relatório automático cancelado.")
        return

    print(f"Executando relatório automático para o chat {TELEGRAM_CHAT_ID}...")
    data_hoje = pd.Timestamp.now(tz=TIMEZONE).date()
    texto_relatorio = gerar_texto_relatorio_diario(data_hoje)
    await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=texto_relatorio, parse_mode='Markdown')


# --- FUNÇÕES LEGADAS (sem alterações, apenas para manter a completude) ---
async def definir_estoque(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Esta função permanece a mesma da versão anterior
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
    # Esta função permanece a mesma da versão anterior
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
            await update.message.reply_text("⚠️ Atenção! Estoque de hoje não definido. Use o comando `/estoque`.")
            return
        estoque_sabor = estoque_hoje[estoque_hoje['sabor'] == sabor]
        if estoque_sabor.empty:
            await update.message.reply_text(f"⚠️ Atenção! Não há estoque inicial para '{sabor.capitalize()}' hoje.")
            return
        estoque_inicial = estoque_sabor['quantidade_inicial'].iloc[0]
        vendas_fid = get_file_id(service, DRIVE_VENDAS_FILE, DRIVE_FOLDER_ID)
        colunas_vendas = ['data_hora', 'sabor', 'quantidade', 'preco_unidade', 'custo_unidade', 'total_venda',
                          'lucro_venda']
        df_vendas = download_dataframe(service, DRIVE_VENDAS_FILE, vendas_fid, colunas_vendas)
        vendas_hoje_sabor = df_vendas[
            (df_vendas['data_hora'].dt.tz_convert(TIMEZONE).dt.date == hoje) & (df_vendas['sabor'] == sabor)]
        ja_vendido = vendas_hoje_sabor['quantidade'].sum()
        estoque_atual = estoque_inicial - ja_vendido
        if quantidade_venda > estoque_atual:
            await update.message.reply_text(f"❌ Venda não registrada! Estoque insuficiente.\n"
                                            f"**Estoque atual de {sabor.capitalize()}:** {int(estoque_atual)} unidades.")
            return
        preco_unidade = PRECO_FIXO_VENDA
        custo_unidade = PRECO_FIXO_CUSTO
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
        await update.message.reply_text('❌ *Erro!* Formato: `/venda [sabor] [quantidade]`\nEx: /venda carne 5',
                                        parse_mode='Markdown')
    except Exception as e:
        print(
            f"--- ERRO INESPERADO EM registrar_venda ---\n{traceback.format_exc()}\n----------------------------------------")
        await update.message.reply_text(f"🐛 Erro inesperado no servidor: `{e}`")


async def relatorio_lucro_periodo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Esta função permanece a mesma da versão anterior
    try:
        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text("❌ Erro! Use o formato: `/lucro [dias]`\nExemplo: `/lucro 7`")
            return
        dias = int(context.args[0])
        hoje = pd.Timestamp.now(tz=TIMEZONE).date()
        data_inicio = hoje - timedelta(days=dias - 1)
        await update.message.reply_text(f"Gerando relatório de lucro dos últimos {dias} dias...")
        service = get_drive_service()
        vendas_fid = get_file_id(service, DRIVE_VENDAS_FILE, DRIVE_FOLDER_ID)
        colunas_vendas = ['data_hora', 'sabor', 'quantidade', 'preco_unidade', 'custo_unidade', 'total_venda',
                          'lucro_venda']
        df = download_dataframe(service, DRIVE_VENDAS_FILE, vendas_fid, colunas_vendas)
        if df.empty:
            await update.message.reply_text("Nenhuma venda encontrada para gerar relatórios.")
            return
        df_periodo = df[(df['data_hora'].dt.tz_convert(TIMEZONE).dt.date >= data_inicio) & (
                    df['data_hora'].dt.tz_convert(TIMEZONE).date <= hoje)]
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
    # Esta função permanece a mesma da versão anterior
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


# --- FUNÇÃO PRINCIPAL E AGENDADOR ---

def main() -> None:
    """Inicia o bot, registra os handlers e o agendador de tarefas."""
    if not TELEGRAM_TOKEN:
        raise ValueError("ERRO: Variável de ambiente TELEGRAM_TOKEN não configurada.")

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Registra todos os comandos
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("registrar", registrar_usuario))
    application.add_handler(CommandHandler("estoque", definir_estoque))
    application.add_handler(CommandHandler("venda", registrar_venda))
    application.add_handler(CommandHandler("diario", relatorio_diario_handler))
    application.add_handler(CommandHandler("lucro", relatorio_lucro_periodo))
    application.add_handler(CommandHandler("vendas", enviar_csv))
    application.add_handler(CommandHandler("ver_estoque", ver_estoque_atual))
    application.add_handler(CommandHandler("grafico", gerar_grafico))

    # Configura e inicia o agendador
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    scheduler.add_job(enviar_relatorio_automatico, 'cron', hour=19, minute=30, args=[application])
    scheduler.start()

    print("Bot com Gráficos e Relatórios Automáticos (v6) iniciado e escutando...")
    application.run_polling()


if __name__ == '__main__':
    main()