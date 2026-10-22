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


def calcular_fatores(precos: pd.DataFrame, volume_financeiro: pd.DataFrame,
                     cfg: dict) -> dict[str, pd.DataFrame]:
    """Séries diárias de cada fator. Cada linha usa só dados até aquela data."""
    fat = cfg["fatores"]
    return {
        "preco": precos,
        "momentum": momentum(precos, fat["janela_momentum"], fat["pular_recente"]),
        "volatilidade": volatilidade(precos, fat["janela_volatilidade"]),
        "liquidez": liquidez(volume_financeiro, fat["janela_volatilidade"]),
    }


def pontuar(precos: pd.DataFrame, volume_financeiro: pd.DataFrame, data: pd.Timestamp,
            cfg: dict, fatores: dict[str, pd.DataFrame] | None = None) -> pd.DataFrame:
    """Calcula fatores e pontuação usando apenas dados até `data` (inclusive).

    `fatores` (de `calcular_fatores`) evita recalcular tudo a cada data no backtest.
    Retorna DataFrame (uma linha por ação elegível) ordenado da melhor para a pior.
    """
    fat, filt = cfg["fatores"], cfg["filtros"]
    if fatores is None:
        # Só as últimas linhas importam para as janelas.
        linhas = max(fat["janela_momentum"], fat["janela_volatilidade"]) + 2
        fatores = calcular_fatores(precos.loc[:data].iloc[-linhas:],
                                   volume_financeiro.loc[:data].iloc[-linhas:], cfg)
    tabela = pd.DataFrame({nome: serie.loc[:data].iloc[-1] for nome, serie in fatores.items()})
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


def abaixo_da_media(precos: pd.Series, data: pd.Timestamp, dias: int = 200) -> bool:
    """True se o preço em `data` está abaixo da média móvel de `dias` pregões."""
    hist = precos.loc[:data].dropna()
    if len(hist) < dias:
        return False  # histórico insuficiente: não bloqueia
    return bool(hist.iloc[-1] < hist.iloc[-dias:].mean())
