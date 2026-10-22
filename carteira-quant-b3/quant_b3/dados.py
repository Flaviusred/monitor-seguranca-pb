"""Obtenção de preços ajustados e volume financeiro."""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pandas as pd

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
VALIDADE_CACHE_S = 12 * 3600


def _com_sufixo(ticker: str) -> str:
    return ticker if ticker.endswith(".SA") or ticker.startswith("^") else f"{ticker}.SA"


def _sem_sufixo(ticker: str) -> str:
    return ticker.removesuffix(".SA")


def baixar_yahoo(tickers: list[str], inicio: str, fim: str | None = None,
                 usar_cache: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Baixa fechamento ajustado (proventos e desdobramentos) e volume financeiro.

    Retorna (precos, volume_financeiro), ambos com datas no índice e tickers
    (sem ".SA") nas colunas.
    """
    chave = hashlib.sha1(f"{sorted(tickers)}|{inicio}|{fim}".encode()).hexdigest()[:12]
    arq_precos = CACHE_DIR / f"precos_{chave}.csv"
    arq_volume = CACHE_DIR / f"volume_{chave}.csv"
    if usar_cache and arq_precos.exists() and time.time() - arq_precos.stat().st_mtime < VALIDADE_CACHE_S:
        return ler_csv(arq_precos), ler_csv(arq_volume)

    import yfinance as yf  # importado aqui para os testes não dependerem dele

    bruto = yf.download([_com_sufixo(t) for t in tickers], start=inicio, end=fim,
                        auto_adjust=True, progress=False, group_by="column")
    if bruto.empty:
        raise RuntimeError("Nenhum dado retornado pelo Yahoo Finance.")
    precos = bruto["Close"].rename(columns=_sem_sufixo)
    volume = (bruto["Close"] * bruto["Volume"]).rename(columns=_sem_sufixo)

    vazios = [t for t in precos.columns if precos[t].isna().all()]
    if vazios:
        print(f"Aviso: sem dados para {', '.join(vazios)} (descartados).")
        precos = precos.drop(columns=vazios)
        volume = volume.drop(columns=vazios)

    precos.index = pd.to_datetime(precos.index).tz_localize(None)
    volume.index = precos.index
    CACHE_DIR.mkdir(exist_ok=True)
    precos.to_csv(arq_precos)
    volume.to_csv(arq_volume)
    return precos, volume


def ler_csv(caminho: str | Path) -> pd.DataFrame:
    """Lê CSV largo: coluna de data + uma coluna por ticker."""
    df = pd.read_csv(caminho, index_col=0, parse_dates=True)
    return df.sort_index()
