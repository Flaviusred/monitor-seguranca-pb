"""Backtest com rebalanceamento periódico, custos de transação e execução defasada."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .carteira import pesos_alvo
from .cdi import retornos_por_pregao
from .fatores import abaixo_da_media, calcular_fatores, pontuar
from .ir import ApuracaoIR


@dataclass
class ResultadoBacktest:
    patrimonio: pd.Series
    rebalanceamentos: pd.DataFrame          # data_sinal, data_execucao, giro, custo, em_caixa, ativos
    pesos: dict[pd.Timestamp, pd.Series] = field(default_factory=dict)
    impostos: pd.DataFrame = field(default_factory=pd.DataFrame)   # apuração mensal de IR
    # Imposto que seria pago vendendo tudo em cada data (0 sem IR ativo).
    imposto_latente: pd.Series = field(default_factory=pd.Series)

    def patrimonio_liquido(self) -> pd.Series:
        """Patrimônio se tudo fosse vendido em cada data, já descontado o IR."""
        return self.patrimonio - self.imposto_latente.reindex(self.patrimonio.index).fillna(0.0)


def datas_sinal(indice: pd.DatetimeIndex, aquecimento: int) -> list[pd.Timestamp]:
    """Último pregão de cada mês, após o período de aquecimento dos fatores."""
    validas = indice[aquecimento:]
    serie = pd.Series(validas, index=validas)
    return list(serie.groupby([validas.year, validas.month]).max())


def executar(precos: pd.DataFrame, volume_financeiro: pd.DataFrame, cfg: dict,
             retorno_caixa: pd.Series | None = None,
             benchmark: pd.Series | None = None,
             fatores: dict[str, pd.DataFrame] | None = None) -> ResultadoBacktest:
    """Simula a estratégia.

    retorno_caixa: rendimento do caixa por pregão (CDI); sem ele, usa a taxa fixa
    `taxa_livre_risco_anual`. benchmark: preços usados pelo filtro de tendência.
    fatores: resultado de `calcular_fatores`, para reaproveitar entre várias simulações.
    """
    bt, cart = cfg["backtest"], cfg["carteira"]
    tendencia = cfg.get("tendencia", {})
    if tendencia.get("ativo") and benchmark is None:
        raise ValueError("Filtro de tendência ativo, mas sem preços do benchmark.")
    volume_financeiro = volume_financeiro.reindex(index=precos.index, columns=precos.columns)
    indice = precos.index
    # Variação diária para avaliar posições; dias sem negociação contam como 0%.
    precos_aval = precos.ffill()
    variacoes = (precos_aval / precos_aval.shift(1)).fillna(1.0).to_numpy()
    if retorno_caixa is None:
        retorno_caixa = retornos_por_pregao(None, indice, bt["taxa_livre_risco_anual"])
    retorno_caixa = retorno_caixa.reindex(indice).fillna(0.0).to_numpy()

    if fatores is None:
        fatores = calcular_fatores(precos, volume_financeiro, cfg)
    aquecimento = cfg["fatores"]["janela_momentum"] + 1
    atraso = bt["atraso_execucao"]
    inicio = pd.Timestamp(bt["inicio"])
    sinais = [d for d in datas_sinal(indice, aquecimento) if d >= inicio]
    sinais = sinais[::cart.get("frequencia_meses", 1)]
    agenda: dict[pd.Timestamp, pd.Timestamp] = {}
    for d in sinais:
        pos = indice.get_loc(d) + atraso
        if pos < len(indice):
            agenda[indice[pos]] = d

    custo_taxa = bt["custo_bps"] / 10_000
    caixa = float(bt["capital_inicial"])
    colunas = precos.columns
    posicoes = np.zeros(len(colunas))   # valor em R$ por ação
    patrimonio, latente, registros, historico_pesos = [], [], [], {}
    impostos = cfg.get("impostos", {})
    ir = (ApuracaoIR(len(colunas), impostos.get("aliquota", 0.15),
                     impostos.get("isencao_mensal_vendas", 20_000.0))
          if impostos.get("ativo") else None)

    for i, d in enumerate(indice):
        if i > 0:
            posicoes *= variacoes[i]
            caixa *= 1 + retorno_caixa[i]
            if ir is not None and d.month != indice[i - 1].month:
                # DARF do mês anterior. Pago do caixa; se faltar, o rebalanceamento
                # seguinte reduz as posições para cobrir.
                caixa -= ir.fechar_mes(indice[i - 1].to_period("M"))

        if d in agenda:
            data_sinal = agenda[d]
            em_caixa = bool(tendencia.get("ativo")) and abaixo_da_media(
                benchmark, data_sinal, tendencia.get("media_dias", 200))
            if em_caixa:
                pesos = pd.Series(dtype=float)
            else:
                ranking = pontuar(precos, volume_financeiro, data_sinal, cfg, fatores)
                atuais = colunas[posicoes > 0]
                pesos = pesos_alvo(ranking, cart["num_ativos"], cart["ponderacao"],
                                   atuais, cart.get("faixa_manutencao"))
                # Só compra o que tem preço no dia da execução.
                pesos = pesos[precos.loc[d, pesos.index].notna()]
                pesos = pesos / pesos.sum() if len(pesos) else pesos

            total = posicoes.sum() + caixa
            vetor_pesos = pesos.reindex(colunas, fill_value=0.0).to_numpy()
            giro = np.abs(vetor_pesos * total - posicoes).sum()
            custo = giro * custo_taxa
            total -= custo
            novas = vetor_pesos * total
            if ir is not None:
                ir.operar(posicoes, novas)
            posicoes = novas
            caixa = total - posicoes.sum()
            historico_pesos[d] = pesos
            registros.append({"data_sinal": data_sinal, "data_execucao": d,
                              "giro": giro / (total + custo), "custo": custo,
                              "em_caixa": em_caixa, "ativos": ", ".join(pesos.index)})

        patrimonio.append(posicoes.sum() + caixa)
        latente.append(ir.imposto_latente(posicoes) if ir is not None else 0.0)

    serie = pd.Series(patrimonio, index=indice, name="patrimonio")
    serie_latente = pd.Series(latente, index=indice, name="imposto_latente")
    if agenda:
        serie = serie.loc[min(agenda):]  # descarta o aquecimento
        serie_latente = serie_latente.loc[min(agenda):]
    tabela_ir = ir.tabela() if ir is not None else pd.DataFrame()
    return ResultadoBacktest(serie, pd.DataFrame(registros), historico_pesos, tabela_ir, serie_latente)
