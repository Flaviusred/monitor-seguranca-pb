"""Taxa CDI diária (Banco Central, série SGS 12): rendimento do caixa e referência do Sharpe."""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

from .dados import CACHE_DIR, VALIDADE_CACHE_S

URL_SGS = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados"
ANOS_POR_CONSULTA = 5  # a API limita o intervalo de séries diárias por consulta


def baixar_cdi(inicio: str, usar_cache: bool = True) -> pd.Series | None:
    """CDI diário em decimal (0,0004 = 0,04% ao dia), indexado por data.

    Retorna None se a API do Banco Central não responder.
    """
    arquivo = CACHE_DIR / "cdi.csv"
    if usar_cache and arquivo.exists() and time.time() - arquivo.stat().st_mtime < VALIDADE_CACHE_S:
        serie = pd.read_csv(arquivo, index_col=0, parse_dates=True).iloc[:, 0]
        if serie.index[0] <= pd.Timestamp(inicio) + pd.Timedelta(days=7):
            return serie

    import requests

    partes = []
    ini, hoje = pd.Timestamp(inicio), pd.Timestamp.today().normalize()
    try:
        while ini <= hoje:
            fim = min(ini + pd.DateOffset(years=ANOS_POR_CONSULTA) - pd.Timedelta(days=1), hoje)
            resp = requests.get(URL_SGS, timeout=30, params={
                "formato": "json", "dataInicial": f"{ini:%d/%m/%Y}", "dataFinal": f"{fim:%d/%m/%Y}"})
            resp.raise_for_status()
            partes.extend(resp.json())
            ini = fim + pd.Timedelta(days=1)
    except Exception as erro:  # rede, API fora do ar, resposta inesperada
        print(f"Aviso: não foi possível baixar o CDI do Banco Central ({type(erro).__name__}).")
        return None
    if not partes:
        return None

    df = pd.DataFrame(partes)
    serie = pd.Series(df["valor"].astype(float).values / 100,
                      index=pd.to_datetime(df["data"], format="%d/%m/%Y"), name="cdi")
    serie = serie[~serie.index.duplicated()].sort_index()
    CACHE_DIR.mkdir(exist_ok=True)
    serie.to_csv(arquivo)
    return serie


def retornos_por_pregao(cdi: pd.Series | None, indice: pd.DatetimeIndex,
                        taxa_anual_padrao: float) -> pd.Series:
    """Rendimento do CDI entre cada pregão e o anterior (0 no primeiro).

    Sem a série do BC, usa uma taxa anual fixa.
    """
    if cdi is None:
        diaria = (1 + taxa_anual_padrao) ** (1 / 252) - 1
        ret = pd.Series(diaria, index=indice)
    else:
        fator = (1 + cdi).cumprod()
        fator = fator.reindex(fator.index.union(indice)).ffill().reindex(indice)
        ret = fator.pct_change().fillna(0.0)
    ret.iloc[0] = 0.0
    return ret.rename("cdi")
