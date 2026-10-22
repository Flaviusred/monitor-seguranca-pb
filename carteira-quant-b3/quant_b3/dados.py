"""Obtenção de preços ajustados e volume financeiro."""
from __future__ import annotations

import hashlib
import logging
import time
import warnings
from pathlib import Path

import pandas as pd

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
VALIDADE_CACHE_S = 12 * 3600
TENTATIVAS_EXTRAS = 2


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

    precos, volume = _baixar(yf, tickers, inicio, fim, threads=4)
    # Falhas de conexão são comuns com muitos downloads em paralelo: tenta de novo,
    # agora sem paralelismo, só as ações que vieram vazias.
    for _ in range(TENTATIVAS_EXTRAS):
        vazios = [t for t in tickers if precos[t].isna().all()]
        if not vazios:
            break
        print(f"Baixando novamente {len(vazios)} ação(ões) que falharam: {', '.join(vazios)}")
        p2, v2 = _baixar(yf, vazios, inicio, fim, threads=False)
        indice = precos.index.union(p2.index)
        precos, volume = precos.reindex(indice), volume.reindex(indice)
        precos[vazios] = p2.reindex(indice)[vazios]
        volume[vazios] = v2.reindex(indice)[vazios]

    if precos.dropna(how="all").empty:
        raise RuntimeError("Nenhum dado retornado pelo Yahoo Finance. Verifique a conexão.")
    vazios = [t for t in tickers if precos[t].isna().all()]
    if vazios:
        print(f"Aviso: sem dados para {', '.join(vazios)} (descartados). Se for falha de "
              "conexão, rode de novo; se o ticker mudou, atualize o config.toml.")
        precos = precos.drop(columns=vazios)
        volume = volume.drop(columns=vazios)

    precos.index = pd.to_datetime(precos.index).tz_localize(None)
    volume.index = precos.index
    if not vazios:  # não guarda no cache um download incompleto
        CACHE_DIR.mkdir(exist_ok=True)
        precos.to_csv(arq_precos)
        volume.to_csv(arq_volume)
    return precos, volume


def _baixar(yf, tickers: list[str], inicio: str, fim: str | None,
            threads) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Uma chamada ao yfinance; devolve todas as colunas pedidas (vazias se falhar)."""
    # Falhas de uma ação são tratadas aqui (nova tentativa e aviso final), então
    # silencia os avisos e erros que o yfinance imprime por conta própria.
    log = logging.getLogger("yfinance")
    nivel = log.level
    log.setLevel(logging.CRITICAL + 1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            bruto = yf.download([_com_sufixo(t) for t in tickers], start=inicio, end=fim,
                                auto_adjust=True, progress=False, group_by="column",
                                multi_level_index=True, threads=threads, timeout=30)
        finally:
            log.setLevel(nivel)
    if bruto.empty:
        vazio = pd.DataFrame(columns=tickers, index=pd.DatetimeIndex([]), dtype=float)
        return vazio, vazio.copy()
    fechamento = bruto["Close"].rename(columns=_sem_sufixo).reindex(columns=tickers)
    volume = (bruto["Close"] * bruto["Volume"]).rename(columns=_sem_sufixo).reindex(columns=tickers)
    return fechamento, volume


def ler_csv(caminho: str | Path) -> pd.DataFrame:
    """Lê CSV largo: coluna de data + uma coluna por ticker."""
    df = pd.read_csv(caminho, index_col=0, parse_dates=True)
    return df.sort_index()
