# reports.py

"""
Funções para geração de relatórios financeiros, de estoque e gráficos para o PasteisBot.
"""

import pandas as pd
import json
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import io

import config
import google_drive as drive

def gerar_dados_relatorio_diario(data_filtro):
    """
    Gera o relatório do dia especificado, retornando um dicionário com métricas e texto formatado.
    """
    service = drive.get_drive_service()

    # Carrega vendas, estoque e consumo do dia
    vendas_fid = drive.get_file_id(service, config.DRIVE_VENDAS_FILE, config.DRIVE_FOLDER_ID)
    df_vendas = drive.download_dataframe(service, config.DRIVE_VENDAS_FILE, vendas_fid,
                                         ['data_hora', 'sabor', 'quantidade', 'preco_unidade', 'custo_unidade',
                                          'total_venda', 'lucro_venda'])
    df_vendas_dia = df_vendas[df_vendas['data_hora'].dt.tz_convert(config.TIMEZONE).dt.date == data_filtro]

    estoque_fid = drive.get_file_id(service, config.DRIVE_ESTOQUE_FILE, config.DRIVE_FOLDER_ID)
    df_estoque = drive.download_dataframe(service, config.DRIVE_ESTOQUE_FILE, estoque_fid,
                                          ['data', 'sabor', 'quantidade_inicial'])
    df_estoque_dia = df_estoque[df_estoque['data'].dt.date == data_filtro]

    consumo_fid = drive.get_file_id(service, config.DRIVE_CONSUMO_FILE, config.DRIVE_FOLDER_ID)
    colunas_consumo = ['data_hora', 'sabor', 'quantidade', 'custo_total']
    df_consumo = drive.download_dataframe(service, config.DRIVE_CONSUMO_FILE, consumo_fid, colunas_consumo)
    df_consumo_dia = df_consumo[df_consumo['data_hora'].dt.tz_convert(config.TIMEZONE).dt.date == data_filtro]

    faturamento_bruto = df_vendas_dia['total_venda'].sum()
    lucro_margem = df_vendas_dia['lucro_venda'].sum()
    pasteis_vendidos = df_vendas_dia['quantidade'].sum()

    custo_inicial_total = 0
    custo_consumo_pessoal = 0
    resultado_do_dia = lucro_margem
    sobras_dict = {sabor: 0 for sabor in config.SABORES_VALIDOS}

    if not df_estoque_dia.empty:
        custo_inicial_total = df_estoque_dia['quantidade_inicial'].sum() * config.PRECO_FIXO_CUSTO
        custo_consumo_pessoal = df_consumo_dia['custo_total'].sum()
        resultado_do_dia = lucro_margem - custo_consumo_pessoal

        for sabor in config.SABORES_VALIDOS:
            inicial = df_estoque_dia[df_estoque_dia['sabor'] == sabor]['quantidade_inicial'].sum()
            vendido = df_vendas_dia[df_vendas_dia['sabor'] == sabor]['quantidade'].sum()
            consumido = df_consumo_dia[df_consumo_dia['sabor'] == sabor]['quantidade'].sum()
            sobra = inicial - vendido - consumido
            sobras_dict[sabor] = int(sobra)

    titulo = f"📊 *Fechamento do Dia: {data_filtro.strftime('%d/%m/%Y')}*"
    resumo_financeiro = (f"💰 *RESUMO FINANCEIRO*\n"
                         f"  - Faturamento Bruto: *R$ {faturamento_bruto:.2f}*\n"
                         f"  - Lucro (Margem das Vendas): *R$ {lucro_margem:.2f}*")
    gestao_estoque = "📦 *GESTÃO DE ESTOQUE*\n"
    if not df_estoque_dia.empty:
        for sabor in config.SABORES_VALIDOS:
            inicial = df_estoque_dia[df_estoque_dia['sabor'] == sabor]['quantidade_inicial'].sum()
            vendido = df_vendas_dia[df_vendas_dia['sabor'] == sabor]['quantidade'].sum()
            consumido = df_consumo_dia[df_consumo_dia['sabor'] == sabor]['quantidade'].sum()
            gestao_estoque += f"  - `{sabor.capitalize()}`: Ini: {int(inicial)}, Ven: {int(vendido)}, Con: {int(consumido)} ➜ Sobra: *{sobras_dict[sabor]}*\n"
    else:
        gestao_estoque += "_Nenhum estoque inicial definido._"
    resultado_final = "🎯 *RESULTADO FINAL DO DIA*\n"
    if not df_estoque_dia.empty:
        resultado_final += f"  - Lucro das Vendas: `R$ {lucro_margem:.2f}`\n"
        resultado_final += f"  - Custo do Consumo: `R$ -{custo_consumo_pessoal:.2f}`\n"
        resultado_final += "  --------------------------------\n"
        if resultado_do_dia >= 0:
            resultado_final += f"  - Resultado: *🚀 Lucro de R$ {resultado_do_dia:.2f}*"
        else:
            resultado_final += f"  - Resultado: *📉 Prejuízo de R$ {-resultado_do_dia:.2f}*"
    else:
        resultado_final += "_Impossível calcular sem o estoque inicial._"

    texto_final = f"{titulo}\n\n{resumo_financeiro}\n\n{gestao_estoque}\n\n{resultado_final}"

    return {
        "texto": texto_final,
        "data": data_filtro.strftime('%Y-%m-%d'),
        "pasteis_vendidos": int(pasteis_vendidos),
        "faturamento_bruto": float(faturamento_bruto),
        "lucro_margem": float(lucro_margem),
        "custo_investimento": float(custo_inicial_total),
        "custo_consumo": float(custo_consumo_pessoal),
        "resultado_final": float(resultado_do_dia),
        "sobras": json.dumps(sobras_dict)
    }

def gerar_grafico_lucro(dias):
    """
    Gera o gráfico de lucro dos últimos N dias.
    Retorna o buffer da imagem e texto de legenda.
    """
    service = drive.get_drive_service()
    vendas_fid = drive.get_file_id(service, config.DRIVE_VENDAS_FILE, config.DRIVE_FOLDER_ID)
    df_vendas = drive.download_dataframe(service, config.DRIVE_VENDAS_FILE, vendas_fid,
                                         ['data_hora', 'sabor', 'quantidade', 'preco_unidade', 'custo_unidade',
                                          'total_venda', 'lucro_venda'])

    if df_vendas.empty:
        return None, "Nenhuma venda encontrada para gerar o gráfico."

    hoje = pd.Timestamp.now(tz=config.TIMEZONE).date()
    data_inicio = hoje - timedelta(days=dias - 1)
    df_periodo = df_vendas[df_vendas['data_hora'].dt.tz_convert(config.TIMEZONE).dt.date >= data_inicio]

    if df_periodo.empty:
        return None, f"Nenhuma venda nos últimos {dias} dias."

    lucro_por_dia = df_periodo.groupby(df_periodo['data_hora'].dt.tz_convert(config.TIMEZONE).dt.date)[
        'lucro_venda'].sum()

    plt.style.use('seaborn-v0_8-whitegrid')
    fig, ax = plt.subplots(figsize=(12, 7))
    bars = ax.bar(lucro_por_dia.index, lucro_por_dia.values, color='#4A90E2', label='Lucro Diário')

    for bar in bars:
        yval = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2.0, yval, f'R${yval:.2f}', va='bottom' if yval >= 0 else 'top',
                ha='center')

    media_lucro = lucro_por_dia.mean()
    ax.axhline(media_lucro, color='red', linestyle='--', linewidth=2, label=f'Média: R$ {media_lucro:.2f}')

    ax.set_title(f'Lucro Líquido por Dia (Últimos {dias} Dias)', fontsize=16, pad=20)
    ax.set_ylabel('Lucro (R$)', fontsize=12)
    ax.set_xlabel('Data', fontsize=12)
    ax.tick_params(axis='x', rotation=45)
    ax.grid(axis='y', linestyle='--', alpha=0.7)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.legend()
    ax.set_xticklabels([d.strftime('%d/%m') for d in lucro_por_dia.index])
    ax.set_ylim(top=ax.get_ylim()[1] * 1.15)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close(fig)

    total_lucro = lucro_por_dia.sum()
    caption = (f"📈 *Relatório Gráfico de Lucro*\n\n"
               f"▫️ Período Analisado: *Últimos {dias} dias*\n"
               f"▫️ Lucro Total no Período: *R$ {total_lucro:.2f}*\n"
               f"▫️ Média de Lucro Diário: *R$ {media_lucro:.2f}*")

    return buf, caption