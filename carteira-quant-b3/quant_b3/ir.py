"""Apuração mensal de IR sobre ações (swing trade) dentro do backtest.

Regras simplificadas (pessoa física, mercado à vista):
- 15% sobre o lucro líquido do mês;
- isento se o total de VENDAS de ações no mês for até R$ 20 mil;
- prejuízos (inclusive de meses isentos) são compensados nos meses seguintes;
- custo de aquisição pelo preço médio.
Não considera custos operacionais no preço médio, IRRF de 0,005%, day trade,
JCP nem tributação de dividendos.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


class ApuracaoIR:
    def __init__(self, n_ativos: int, aliquota: float = 0.15, isencao_vendas: float = 20_000.0):
        self.aliquota = aliquota
        self.isencao_vendas = isencao_vendas
        self.custo = np.zeros(n_ativos)   # custo de aquisição (R$) da posição atual de cada ação
        self.ganho_mes = 0.0
        self.vendas_mes = 0.0
        self.prejuizo = 0.0               # prejuízo acumulado a compensar (>= 0)
        self.historico: list[dict] = []

    def operar(self, antes: np.ndarray, depois: np.ndarray) -> None:
        """Registra o rebalanceamento: valor (R$) de cada posição antes e depois."""
        venda = np.clip(antes - depois, 0.0, None)
        compra = np.clip(depois - antes, 0.0, None)
        with np.errstate(divide="ignore", invalid="ignore"):
            fracao = np.where(antes > 0, venda / antes, 0.0)
        self.ganho_mes += float((fracao * (antes - self.custo)).sum())
        self.custo = self.custo * (1 - fracao) + compra
        self.vendas_mes += float(venda.sum())

    def fechar_mes(self, mes: pd.Period) -> float:
        """Apura o mês e devolve o imposto devido (DARF)."""
        isento = self.vendas_mes <= self.isencao_vendas
        tributavel = min(self.ganho_mes, 0.0) if isento else self.ganho_mes
        if tributavel > 0:
            compensado = min(self.prejuizo, tributavel)
            self.prejuizo -= compensado
            imposto = self.aliquota * (tributavel - compensado)
        else:
            self.prejuizo -= tributavel
            imposto = 0.0
        if self.vendas_mes or self.ganho_mes:
            self.historico.append({"mes": mes, "vendas": self.vendas_mes, "resultado": self.ganho_mes,
                                   "isento": isento, "imposto": imposto,
                                   "prejuizo_acumulado": self.prejuizo})
        self.ganho_mes = self.vendas_mes = 0.0
        return imposto

    def imposto_latente(self, posicoes: np.ndarray) -> float:
        """Imposto que seria devido vendendo tudo agora (sem isenção: venda grande)."""
        ganho = float((posicoes - self.custo).sum()) + self.ganho_mes - self.prejuizo
        return self.aliquota * max(ganho, 0.0)

    def tabela(self) -> pd.DataFrame:
        return pd.DataFrame(self.historico, columns=["mes", "vendas", "resultado", "isento",
                                                     "imposto", "prejuizo_acumulado"])
