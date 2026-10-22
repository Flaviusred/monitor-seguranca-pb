"""Cálculo dos fatores e da pontuação de cada ação."""
from __future__ import annotations

import numpy as np
import pandas as pd


def momentum(precos: pd.DataFrame, janela: int = 252, pular: int = 21) -> pd.DataFrame:
    """Retorno de t-janela até t-pular (momentum "12-1")."""
    return precos.shift(pular) / precos.shift(janela) - 1


def volatilidade(precos: pd.DataFrame, janela: int = 63) -> pd.DataFrame:
    """Volatilidade anualizada dos retornos diários."""
    return precos.pct_change(fill_method=None).rolling(janela, min_periods=janela // 2).std() * np.sqrt(252)


def liquidez(volume_financeiro: pd.DataFrame, janela: int = 63) -> pd.DataFrame:
    return volume_financeiro.rolling(janela, min_periods=janela // 2).mean()


def zscore(serie: pd.Series, limite: float = 3.0) -> pd.Series:
    """Padroniza entre ações (cross-section) e limita outliers."""
    desvio = serie.std()
    if not desvio or np.isnan(desvio):
        return serie * 0.0
    return ((serie - serie.mean()) / desvio).clip(-limite, limite)


def pontuar(precos: pd.DataFrame, volume_financeiro: pd.DataFrame, data: pd.Timestamp,
            cfg: dict) -> pd.DataFrame:
    """Calcula fatores e pontuação usando apenas dados até `data` (inclusive).

    Retorna DataFrame (uma linha por ação elegível) ordenado da melhor para a pior.
    """
    fat, filt = cfg["fatores"], cfg["filtros"]
    hist = precos.loc[:data]
    vol_fin = volume_financeiro.loc[:data]

    tabela = pd.DataFrame({
        "preco": hist.iloc[-1],
        "momentum": momentum(hist, fat["janela_momentum"], fat["pular_recente"]).iloc[-1],
        "volatilidade": volatilidade(hist, fat["janela_volatilidade"]).iloc[-1],
        "liquidez": liquidez(vol_fin, fat["janela_volatilidade"]).iloc[-1],
    })
    elegiveis = (
        tabela["preco"].notna()  # negociou na data (exclui ações deslistadas)
        & (tabela["preco"] >= filt["preco_minimo"])
        & (tabela["liquidez"] >= filt["liquidez_minima"])
        & tabela["momentum"].notna()
        & tabela["volatilidade"].notna()
    )
    tabela = tabela[elegiveis].copy()
    if tabela.empty:
        return tabela.assign(pontuacao=pd.Series(dtype=float))

    tabela["pontuacao"] = (fat["peso_momentum"] * zscore(tabela["momentum"])
                           - fat["peso_volatilidade"] * zscore(tabela["volatilidade"]))
    return tabela.sort_values("pontuacao", ascending=False)
