# main.py

"""
Arquivo principal de inicialização do PasteisBot.

Responsável por registrar handlers, iniciar o agendador de tarefas e rodar o bot.
"""

from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    CallbackQueryHandler,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
import handlers
from reports import gerar_dados_relatorio_diario

async def post_init(application: Application) -> None:
    """
    Função para iniciar o agendador após o bot ligar.
    Envia relatório automático para o chat configurado.
    """
    scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)

    async def job():
        if not config.TELEGRAM_CHAT_ID:
            print("TELEGRAM_CHAT_ID não definido. Relatório automático cancelado.")
            return
        print(f"Executando relatório automático para o chat {config.TELEGRAM_CHAT_ID}...")
        data_hoje = pd.Timestamp.now(tz=config.TIMEZONE).date()
        dados = gerar_dados_relatorio_diario(data_hoje)
        await application.bot.send_message(chat_id=config.TELEGRAM_CHAT_ID, text=dados['texto'], parse_mode='Markdown')

    scheduler.add_job(job, 'cron', hour=19, minute=30)
    scheduler.start()
    print("Agendador de tarefas iniciado e configurado para 19:30.")

def register_handlers(application):
    """
    Registra todos os handlers do bot.
    """
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("fechamento", handlers.fechamento_diario)],
        states={
            config.ASK_CARRYOVER: [CallbackQueryHandler(handlers.handle_carryover_choice)]
        },
        fallbacks=[CommandHandler("cancelar", handlers.cancel)],
    )
    application.add_handler(conv_handler)

    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("registrar", handlers.registrar_usuario))
    application.add_handler(CommandHandler("estoque", handlers.definir_estoque))
    application.add_handler(CommandHandler("venda", handlers.registrar_venda))
    application.add_handler(CommandHandler("consumo", handlers.consumo_pessoal))
    application.add_handler(CommandHandler("diario", handlers.relatorio_diario_handler))
    application.add_handler(CommandHandler("lucro", handlers.relatorio_lucro_periodo))
    application.add_handler(CommandHandler("vendas", handlers.enviar_csv))
    application.add_handler(CommandHandler("ver_estoque", handlers.ver_estoque_atual))
    application.add_handler(CommandHandler("grafico", handlers.gerar_grafico))

def main() -> None:
    """
    Inicia o bot e registra todos os handlers.
    """
    if not config.TELEGRAM_TOKEN:
        raise ValueError("ERRO: Variável de ambiente TELEGRAM_TOKEN não configurada.")

    application = Application.builder().token(config.TELEGRAM_TOKEN).post_init(post_init).build()

    register_handlers(application)

    print("Bot Modular (v13) iniciado...")
    application.run_polling()

if __name__ == '__main__':
    main()