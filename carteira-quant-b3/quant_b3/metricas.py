"""Métricas de desempenho de uma curva de patrimônio."""
from __future__ import annotations

import numpy as np
import pandas as pd


def drawdown(patrimonio: pd.Series) -> pd.Series:
    return patrimonio / patrimonio.cummax() - 1


def resumo(patrimonio: pd.Series, taxa_livre_risco_anual: float = 0.0) -> dict[str, float]:
    patrimonio = patrimonio.dropna()
    retornos = patrimonio.pct_change().dropna()
    anos = (patrimonio.index[-1] - patrimonio.index[0]).days / 365.25
    cagr = (patrimonio.iloc[-1] / patrimonio.iloc[0]) ** (1 / anos) - 1 if anos > 0 else np.nan
    vol = retornos.std() * np.sqrt(252)
    rf_diaria = (1 + taxa_livre_risco_anual) ** (1 / 252) - 1
    excesso = retornos - rf_diaria
    sharpe = excesso.mean() / excesso.std() * np.sqrt(252) if excesso.std() else np.nan
    mensal = patrimonio.resample("ME").last().pct_change().dropna()
    return {
        "retorno_total": patrimonio.iloc[-1] / patrimonio.iloc[0] - 1,
        "cagr": cagr,
        "volatilidade": vol,
        "sharpe": sharpe,
        "max_drawdown": drawdown(patrimonio).min(),
        "meses_positivos": (mensal > 0).mean() if len(mensal) else np.nan,
    }
