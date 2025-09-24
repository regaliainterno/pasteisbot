# handlers.py
import pandas as pd
from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
import json
from datetime import datetime, timedelta
import traceback
import io

import config
import google_drive as drive
import reports


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_name = update.effective_user.first_name
    help_text = (
        f"Olá, {user_name}! Bem-vindo ao seu assistente de gestão v12 (Modular)!\n\n"
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


async def registrar_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"✅ Chat registrado.\n\n"
        f"Para relatórios automáticos, adicione a variável `TELEGRAM_CHAT_ID` no Railway com este valor:\n`{chat_id}`"
    )


async def definir_estoque(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not context.args or len(context.args) % 2 != 0:
            await update.message.reply_text("❌ Erro! Formato: `/estoque [sabor1] [qtd1]...`")
            return

        hoje_str = pd.Timestamp.now(tz=config.TIMEZONE).strftime('%Y-%m-%d')
        await update.message.reply_text("Atualizando estoque do dia...")
        service = drive.get_drive_service()

        estoque_fid = drive.get_file_id(service, config.DRIVE_ESTOQUE_FILE, config.DRIVE_FOLDER_ID)
        df_estoque = drive.download_dataframe(service, config.DRIVE_ESTOQUE_FILE, estoque_fid,
                                              ['data', 'sabor', 'quantidade_inicial'])
        df_estoque['data'] = pd.to_datetime(df_estoque['data']).dt.strftime('%Y-%m-%d')

        resumo_estoque = []
        for i in range(0, len(context.args), 2):
            sabor = context.args[i].lower()
            quantidade = int(context.args[i + 1])
            if sabor not in config.SABORES_VALIDOS:
                await update.message.reply_text(f"Sabor '{sabor}' inválido. Ignorando.")
                continue

            df_estoque = df_estoque[~((df_estoque['data'] == hoje_str) & (df_estoque['sabor'] == sabor))]
            novo_estoque = pd.DataFrame([{'data': hoje_str, 'sabor': sabor, 'quantidade_inicial': quantidade}])
            df_estoque = pd.concat([df_estoque, novo_estoque], ignore_index=True)
            resumo_estoque.append(f"  - {sabor.capitalize()}: {quantidade} unidades")

        drive.upload_dataframe(service, df_estoque, config.DRIVE_ESTOQUE_FILE, estoque_fid, config.DRIVE_FOLDER_ID)
        mensagem_resumo = "✅ Estoque inicial de hoje definido:\n" + "\n".join(resumo_estoque)
        await update.message.reply_text(mensagem_resumo)
    except Exception as e:
        await update.message.reply_text(f"🐛 Erro inesperado ao definir estoque: {e}")


async def registrar_venda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if len(context.args) != 2: raise ValueError("Formato incorreto")
        sabor = context.args[0].lower()
        quantidade_venda = int(context.args[1])
        if sabor not in config.SABORES_VALIDOS:
            sabores_str = ", ".join(config.SABORES_VALIDOS)
            await update.message.reply_text(f"❌ Sabor inválido. Use: *{sabores_str}*.", parse_mode='Markdown')
            return

        hoje = pd.Timestamp.now(tz=config.TIMEZONE).date()
        service = drive.get_drive_service()

        estoque_fid = drive.get_file_id(service, config.DRIVE_ESTOQUE_FILE, config.DRIVE_FOLDER_ID)
        df_estoque = drive.download_dataframe(service, config.DRIVE_ESTOQUE_FILE, estoque_fid,
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

        vendas_fid = drive.get_file_id(service, config.DRIVE_VENDAS_FILE, config.DRIVE_FOLDER_ID)
        df_vendas = drive.download_dataframe(service, config.DRIVE_VENDAS_FILE, vendas_fid,
                                             ['data_hora', 'sabor', 'quantidade'])
        vendas_hoje_sabor = df_vendas[
            (df_vendas['data_hora'].dt.tz_convert(config.TIMEZONE).dt.date == hoje) & (df_vendas['sabor'] == sabor)]
        ja_vendido = vendas_hoje_sabor['quantidade'].sum()

        consumo_fid = drive.get_file_id(service, config.DRIVE_CONSUMO_FILE, config.DRIVE_FOLDER_ID)
        colunas_consumo = ['data_hora', 'sabor', 'quantidade', 'custo_total']
        df_consumo = drive.download_dataframe(service, config.DRIVE_CONSUMO_FILE, consumo_fid, colunas_consumo)
        consumo_hoje_sabor = df_consumo[
            (df_consumo['data_hora'].dt.tz_convert(config.TIMEZONE).dt.date == hoje) & (df_consumo['sabor'] == sabor)]
        ja_consumido = consumo_hoje_sabor['quantidade'].sum()

        estoque_atual = estoque_inicial - ja_vendido - ja_consumido

        if quantidade_venda > estoque_atual:
            await update.message.reply_text(f"❌ Venda não registrada! Estoque insuficiente: *{int(estoque_atual)}*.",
                                            parse_mode='Markdown')
            return

        preco_unidade, custo_unidade = config.PRECO_FIXO_VENDA, config.PRECO_FIXO_CUSTO
        total_venda = quantidade_venda * preco_unidade
        lucro_venda = total_venda - (quantidade_venda * custo_unidade)

        nova_venda = pd.DataFrame(
            [{'data_hora': pd.to_datetime('now', utc=True), 'sabor': sabor, 'quantidade': quantidade_venda,
              'preco_unidade': preco_unidade, 'custo_unidade': custo_unidade, 'total_venda': total_venda,
              'lucro_venda': lucro_venda}])
        df_vendas = pd.concat([df_vendas, nova_venda], ignore_index=True)
        drive.upload_dataframe(service, df_vendas, config.DRIVE_VENDAS_FILE, vendas_fid, config.DRIVE_FOLDER_ID)

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
        if sabor not in config.SABORES_VALIDOS:
            await update.message.reply_text(f"❌ Sabor inválido: *{sabor}*.", parse_mode='Markdown')
            return

        hoje = pd.Timestamp.now(tz=config.TIMEZONE).date()
        service = drive.get_drive_service()

        estoque_fid = drive.get_file_id(service, config.DRIVE_ESTOQUE_FILE, config.DRIVE_FOLDER_ID)
        df_estoque = drive.download_dataframe(service, config.DRIVE_ESTOQUE_FILE, estoque_fid,
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

        vendas_fid = drive.get_file_id(service, config.DRIVE_VENDAS_FILE, config.DRIVE_FOLDER_ID)
        df_vendas = drive.download_dataframe(service, config.DRIVE_VENDAS_FILE, vendas_fid,
                                             ['data_hora', 'sabor', 'quantidade'])
        vendas_hoje_sabor = df_vendas[
            (df_vendas['data_hora'].dt.tz_convert(config.TIMEZONE).dt.date == hoje) & (df_vendas['sabor'] == sabor)]
        ja_vendido = vendas_hoje_sabor['quantidade'].sum()

        consumo_fid = drive.get_file_id(service, config.DRIVE_CONSUMO_FILE, config.DRIVE_FOLDER_ID)
        colunas_consumo = ['data_hora', 'sabor', 'quantidade', 'custo_total']
        df_consumo = drive.download_dataframe(service, config.DRIVE_CONSUMO_FILE, consumo_fid, colunas_consumo)
        consumo_hoje_sabor = df_consumo[
            (df_consumo['data_hora'].dt.tz_convert(config.TIMEZONE).dt.date == hoje) & (df_consumo['sabor'] == sabor)]
        ja_consumido = consumo_hoje_sabor['quantidade'].sum()

        estoque_atual = estoque_inicial - ja_vendido - ja_consumido

        if quantidade_consumo > estoque_atual:
            await update.message.reply_text(f"❌ Consumo não registrado! Estoque insuficiente: *{int(estoque_atual)}*.",
                                            parse_mode='Markdown')
            return

        novo_consumo = pd.DataFrame(
            [{'data_hora': pd.to_datetime('now', utc=True), 'sabor': sabor, 'quantidade': quantidade_consumo,
              'custo_total': quantidade_consumo * config.PRECO_FIXO_CUSTO}])
        df_consumo = pd.concat([df_consumo, novo_consumo], ignore_index=True)
        drive.upload_dataframe(service, df_consumo, config.DRIVE_CONSUMO_FILE, consumo_fid, config.DRIVE_FOLDER_ID)

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
            data_filtro = pd.Timestamp.now(tz=config.TIMEZONE).date()

        dados = reports.gerar_dados_relatorio_diario(data_filtro)
        await update.message.reply_text(dados['texto'], parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"🐛 Erro ao gerar relatório: {e}")


async def ver_estoque_atual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        hoje = pd.Timestamp.now(tz=config.TIMEZONE).date()
        service = drive.get_drive_service()

        estoque_fid = drive.get_file_id(service, config.DRIVE_ESTOQUE_FILE, config.DRIVE_FOLDER_ID)
        df_estoque = drive.download_dataframe(service, config.DRIVE_ESTOQUE_FILE, estoque_fid,
                                              ['data', 'sabor', 'quantidade_inicial'])

        vendas_fid = drive.get_file_id(service, config.DRIVE_VENDAS_FILE, config.DRIVE_FOLDER_ID)
        df_vendas = drive.download_dataframe(service, config.DRIVE_VENDAS_FILE, vendas_fid,
                                             ['data_hora', 'sabor', 'quantidade', 'lucro_venda'])

        consumo_fid = drive.get_file_id(service, config.DRIVE_CONSUMO_FILE, config.DRIVE_FOLDER_ID)
        df_consumo = drive.download_dataframe(service, config.DRIVE_CONSUMO_FILE, consumo_fid,
                                              ['data_hora', 'sabor', 'quantidade', 'custo_total'])

        estoque_hoje = df_estoque[df_estoque['data'].dt.date == hoje]

        if estoque_hoje.empty:
            await update.message.reply_text("Estoque de hoje ainda não definido. Use `/estoque`.")
            return

        vendas_hoje = df_vendas[df_vendas['data_hora'].dt.tz_convert(config.TIMEZONE).dt.date == hoje]
        consumo_hoje = df_consumo[df_consumo['data_hora'].dt.tz_convert(config.TIMEZONE).dt.date == hoje]

        vendido_por_sabor = vendas_hoje.groupby('sabor')['quantidade'].sum()
        consumido_por_sabor = consumo_hoje.groupby('sabor')['quantidade'].sum()

        relatorio_texto = "📦 *Estoque Atual*\n\n"
        for index, row in estoque_hoje.iterrows():
            sabor = row['sabor']
            inicial = row['quantidade_inicial']

            vendido = vendido_por_sabor.get(sabor, 0)
            consumido = consumido_por_sabor.get(sabor, 0)

            sobra = inicial - vendido - consumido
            relatorio_texto += f"- {sabor.capitalize()}: *{int(sobra)}* unidades\n"

        await update.message.reply_text(relatorio_texto, parse_mode='Markdown')

    except Exception as e:
        print(
            f"--- ERRO INESPERADO EM ver_estoque_atual ---\n{traceback.format_exc()}\n----------------------------------------")
        await update.message.reply_text(f"🐛 Erro ao verificar estoque: {e}")


async def gerar_grafico(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text("❌ Erro! Formato: `/grafico [dias]`")
            return

        dias = int(context.args[0])
        await update.message.reply_text(f"Gerando gráfico de lucro dos últimos {dias} dias...")

        buffer, caption = reports.gerar_grafico_lucro(dias)

        if buffer:
            await update.message.reply_photo(photo=buffer, caption=caption, parse_mode='Markdown')
        else:
            await update.message.reply_text(caption)  # Envia a mensagem de erro

    except Exception as e:
        print(
            f"--- ERRO INESPERADO EM gerar_grafico ---\n{traceback.format_exc()}\n----------------------------------------")
        await update.message.reply_text(f"🐛 Erro ao gerar gráfico: {e}")


async def relatorio_lucro_periodo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text("❌ Erro! Formato: `/lucro [dias]`")
            return

        dias = int(context.args[0])
        await update.message.reply_text(f"Gerando relatório de lucro dos últimos {dias} dias...")

        service = drive.get_drive_service()
        df = drive.download_dataframe(service, config.DRIVE_VENDAS_FILE,
                                      drive.get_file_id(service, config.DRIVE_VENDAS_FILE, config.DRIVE_FOLDER_ID),
                                      ['data_hora', 'sabor', 'quantidade', 'preco_unidade', 'custo_unidade',
                                       'total_venda', 'lucro_venda'])

        if df.empty:
            await update.message.reply_text("Nenhuma venda encontrada.")
            return

        hoje = pd.Timestamp.now(tz=config.TIMEZONE).date()
        data_inicio = hoje - timedelta(days=dias - 1)
        df_periodo = df[(df['data_hora'].dt.tz_convert(config.TIMEZONE).dt.date >= data_inicio) & (
                    df['data_hora'].dt.tz_convert(config.TIMEZONE).dt.date <= hoje)]

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
        service = drive.get_drive_service()
        vendas_fid = drive.get_file_id(service, config.DRIVE_VENDAS_FILE, config.DRIVE_FOLDER_ID)
        if not vendas_fid:
            await update.message.reply_text("Nenhum arquivo de vendas encontrado.")
            return

        request = service.files().get_media(fileId=vendas_fid)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done: status, done = downloader.next_chunk()
        fh.seek(0)
        await update.message.reply_document(document=InputFile(fh, filename=config.DRIVE_VENDAS_FILE),
                                            caption="Aqui está o seu relatório de vendas completo.")
    except Exception as e:
        await update.message.reply_text(f"Ocorreu um erro ao enviar o arquivo: {e}")


async def fechamento_diario(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        hoje = pd.Timestamp.now(tz=config.TIMEZONE).date()
        await update.message.reply_text(f"🔒 Iniciando fechamento do dia {hoje.strftime('%d/%m/%Y')}...")
        dados_relatorio = reports.gerar_dados_relatorio_diario(hoje)
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
                reply_markup=reply_markup)
            return config.ASK_CARRYOVER
        else:
            # Se não houver sobras, finaliza automaticamente
            await update.message.reply_text("Nenhuma sobra de estoque encontrada. Salvando relatório...")
            service = drive.get_drive_service()
            fechamentos_fid = drive.get_file_id(service, config.DRIVE_FECHAMENTOS_FILE, config.DRIVE_FOLDER_ID)
            colunas_fechamento = list(dados_relatorio.keys())[1:]
            df_fechamentos = drive.download_dataframe(service, config.DRIVE_FECHAMENTOS_FILE, fechamentos_fid,
                                                      colunas_fechamento)
            novo_fechamento_df = pd.DataFrame([dados_relatorio])
            novo_fechamento_df = novo_fechamento_df.drop(columns=['texto'])
            df_fechamentos = pd.concat([df_fechamentos, novo_fechamento_df], ignore_index=True)
            drive.upload_dataframe(service, df_fechamentos, config.DRIVE_FECHAMENTOS_FILE, fechamentos_fid,
                                   config.DRIVE_FOLDER_ID)
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
    service = drive.get_drive_service()
    fechamentos_fid = drive.get_file_id(service, config.DRIVE_FECHAMENTOS_FILE, config.DRIVE_FOLDER_ID)
    colunas_fechamento = list(dados_fechamento.keys())[1:]
    df_fechamentos = drive.download_dataframe(service, config.DRIVE_FECHAMENTOS_FILE, fechamentos_fid,
                                              colunas_fechamento)
    novo_fechamento_df = pd.DataFrame([dados_fechamento])
    novo_fechamento_df = novo_fechamento_df.drop(columns=['texto'])
    df_fechamentos['data'] = pd.to_datetime(df_fechamentos['data']).dt.strftime('%Y-%m-%d')
    df_fechamentos = df_fechamentos[~(df_fechamentos['data'] == dados_fechamento['data'])]
    df_fechamentos = pd.concat([df_fechamentos, novo_fechamento_df], ignore_index=True)
    drive.upload_dataframe(service, df_fechamentos, config.DRIVE_FECHAMENTOS_FILE, fechamentos_fid,
                           config.DRIVE_FOLDER_ID)

    amanha = pd.Timestamp.now(tz=config.TIMEZONE).date() + timedelta(days=1)
    estoque_fid = drive.get_file_id(service, config.DRIVE_ESTOQUE_FILE, config.DRIVE_FOLDER_ID)
    df_estoque = drive.download_dataframe(service, config.DRIVE_ESTOQUE_FILE, estoque_fid,
                                          ['data', 'sabor', 'quantidade_inicial'])

    if choice == "carryover_yes" and sobras:
        for sabor, quantidade in sobras.items():
            if quantidade > 0:
                df_estoque = df_estoque[~((df_estoque['data'].dt.date == amanha) & (df_estoque['sabor'] == sabor))]
                novo_estoque = pd.DataFrame(
                    [{'data': pd.Timestamp(amanha, tz='UTC'), 'sabor': sabor, 'quantidade_inicial': quantidade}])
                df_estoque = pd.concat([df_estoque, novo_estoque], ignore_index=True)
        await query.edit_message_text(text="✅ Fechamento concluído! Relatório salvo e sobras lançadas para amanhã.")
    else:
        sabores_com_sobra = [sabor for sabor, qtd in sobras.items() if qtd > 0]
        if sabores_com_sobra:
            df_estoque = df_estoque[
                ~((df_estoque['data'].dt.date == amanha) & (df_estoque['sabor'].isin(sabores_com_sobra)))]
        await query.edit_message_text(text="✅ Fechamento concluído! Relatório salvo e sobras descartadas.")

    drive.upload_dataframe(service, df_estoque, config.DRIVE_ESTOQUE_FILE, estoque_fid, config.DRIVE_FOLDER_ID)
    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text="Operação cancelada.")
    else:
        await update.message.reply_text("Nenhuma operação em andamento para cancelar.")
    context.user_data.clear()
    return ConversationHandler.END