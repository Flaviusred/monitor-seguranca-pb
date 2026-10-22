"""Métricas de desempenho de uma curva de patrimônio."""
from __future__ import annotations

import numpy as np
import pandas as pd


def drawdown(patrimonio: pd.Series) -> pd.Series:
    return patrimonio / patrimonio.cummax() - 1


def resumo(patrimonio: pd.Series, livre_risco: float | pd.Series = 0.0) -> dict[str, float]:
    """livre_risco: taxa anual fixa ou rendimento por pregão (ex.: CDI) para o Sharpe."""
    patrimonio = patrimonio.dropna()
    retornos = patrimonio.pct_change().dropna()
    anos = (patrimonio.index[-1] - patrimonio.index[0]).days / 365.25
    cagr = (patrimonio.iloc[-1] / patrimonio.iloc[0]) ** (1 / anos) - 1 if anos > 0 else np.nan
    vol = retornos.std() * np.sqrt(252)
    if isinstance(livre_risco, pd.Series):
        rf = livre_risco.reindex(retornos.index).fillna(0.0)
    else:
        rf = (1 + livre_risco) ** (1 / 252) - 1
    excesso = retornos - rf
    # Sem variação em relação ao livre de risco (ex.: o próprio CDI), o Sharpe não se aplica.
    sharpe = excesso.mean() / excesso.std() * np.sqrt(252) if excesso.std() > 1e-10 else np.nan
    mensal = patrimonio.resample("ME").last().pct_change().dropna()
    return {
        "retorno_total": patrimonio.iloc[-1] / patrimonio.iloc[0] - 1,
        "cagr": cagr,
        "volatilidade": vol,
        "sharpe": sharpe,
        "max_drawdown": drawdown(patrimonio).min(),
        "meses_positivos": (mensal > 0).mean() if len(mensal) else np.nan,
    }


def cagr(patrimonio: pd.Series, imposto_final: float = 0.0) -> float:
    """CAGR; `imposto_final` é descontado do valor final (ex.: IR ao vender tudo)."""
    patrimonio = patrimonio.dropna()
    anos = (patrimonio.index[-1] - patrimonio.index[0]).days / 365.25
    if anos <= 0.5:
        return np.nan
    return ((patrimonio.iloc[-1] - imposto_final) / patrimonio.iloc[0]) ** (1 / anos) - 1


def cagr_vendendo_no_fim(patrimonio: pd.Series, aliquota: float) -> float:
    """CAGR de quem compra no início e vende no fim, pagando IR sobre o ganho
    (ex.: BOVA11 ou CDI, que só pagam imposto na venda)."""
    patrimonio = patrimonio.dropna()
    ganho = patrimonio.iloc[-1] - patrimonio.iloc[0]
    return cagr(patrimonio, aliquota * max(ganho, 0.0))
