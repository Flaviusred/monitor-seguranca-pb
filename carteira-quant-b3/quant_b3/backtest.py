"""Backtest com rebalanceamento mensal, custos de transação e execução defasada."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .carteira import pesos_alvo
from .fatores import pontuar


@dataclass
class ResultadoBacktest:
    patrimonio: pd.Series
    rebalanceamentos: pd.DataFrame          # data_sinal, data_execucao, giro, custo, ativos
    pesos: dict[pd.Timestamp, pd.Series] = field(default_factory=dict)


def datas_sinal(indice: pd.DatetimeIndex, aquecimento: int) -> list[pd.Timestamp]:
    """Último pregão de cada mês, após o período de aquecimento dos fatores."""
    validas = indice[aquecimento:]
    serie = pd.Series(validas, index=validas)
    return list(serie.groupby([validas.year, validas.month]).max())


def executar(precos: pd.DataFrame, volume_financeiro: pd.DataFrame, cfg: dict) -> ResultadoBacktest:
    bt, cart = cfg["backtest"], cfg["carteira"]
    volume_financeiro = volume_financeiro.reindex(index=precos.index, columns=precos.columns)
    indice = precos.index
    # Preço para avaliar posições: repete o último preço em dias sem negociação.
    precos_aval = precos.ffill()

    aquecimento = cfg["fatores"]["janela_momentum"] + 1
    atraso = bt["atraso_execucao"]
    agenda: dict[pd.Timestamp, pd.Timestamp] = {}
    inicio = pd.Timestamp(bt["inicio"])
    for d in datas_sinal(indice, aquecimento):
        if d < inicio:
            continue
        pos = indice.get_loc(d) + atraso
        if pos < len(indice):
            agenda[indice[pos]] = d

    custo_taxa = bt["custo_bps"] / 10_000
    caixa = float(bt["capital_inicial"])
    posicoes = pd.Series(0.0, index=precos.columns)   # valor em R$ por ação
    patrimonio, registros, historico_pesos = [], [], {}

    for i, d in enumerate(indice):
        if i > 0:
            variacao = (precos_aval.iloc[i] / precos_aval.iloc[i - 1]).fillna(1.0)
            posicoes = posicoes * variacao

        if d in agenda:
            data_sinal = agenda[d]
            ranking = pontuar(precos, volume_financeiro, data_sinal, cfg)
            pesos = pesos_alvo(ranking, cart["num_ativos"], cart["ponderacao"])
            # Só compra o que tem preço no dia da execução.
            pesos = pesos[precos.loc[d, pesos.index].notna()]
            pesos = pesos / pesos.sum() if len(pesos) else pesos

            total = posicoes.sum() + caixa
            alvo = pesos.reindex(posicoes.index, fill_value=0.0) * total
            giro = (alvo - posicoes).abs().sum()
            custo = giro * custo_taxa
            total -= custo
            posicoes = pesos.reindex(posicoes.index, fill_value=0.0) * total
            caixa = total - posicoes.sum()
            historico_pesos[d] = pesos
            registros.append({"data_sinal": data_sinal, "data_execucao": d,
                              "giro": giro / (total + custo), "custo": custo,
                              "ativos": ", ".join(pesos.index)})

        patrimonio.append(posicoes.sum() + caixa)

    serie = pd.Series(patrimonio, index=indice, name="patrimonio")
    if agenda:
        serie = serie.loc[min(agenda):]  # descarta o aquecimento (100% caixa)
    return ResultadoBacktest(serie, pd.DataFrame(registros), historico_pesos)
