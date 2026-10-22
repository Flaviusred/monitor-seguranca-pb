"""Seleção de ativos, pesos-alvo e geração de ordens de rebalanceamento."""
from __future__ import annotations

import math

import pandas as pd


def pesos_alvo(ranking: pd.DataFrame, num_ativos: int, ponderacao: str = "igual") -> pd.Series:
    """Escolhe os `num_ativos` melhores do ranking e define os pesos (soma = 1)."""
    escolhidos = ranking.head(num_ativos)
    if escolhidos.empty:
        return pd.Series(dtype=float)
    if ponderacao == "igual":
        pesos = pd.Series(1.0, index=escolhidos.index)
    elif ponderacao == "inv_vol":
        pesos = 1.0 / escolhidos["volatilidade"]
    else:
        raise ValueError(f"Ponderação desconhecida: {ponderacao!r}")
    return pesos / pesos.sum()


def gerar_ordens(pesos: pd.Series, posicoes: dict[str, int], caixa: float,
                 precos: pd.Series, lote: int = 1) -> pd.DataFrame:
    """Converte pesos-alvo em ordens de compra/venda com quantidades inteiras.

    Vendas vêm primeiro (liberam caixa). Compras são arredondadas para baixo
    no múltiplo de `lote`, então sempre sobra um pequeno resíduo em caixa.
    """
    tickers = sorted(set(pesos.index) | {t for t, q in posicoes.items() if q})
    faltando = [t for t in tickers if pd.isna(precos.get(t))]
    if faltando:
        raise ValueError(f"Sem preço para: {', '.join(faltando)}")

    patrimonio = caixa + sum(q * precos[t] for t, q in posicoes.items() if q)
    ordens = []
    for t in tickers:
        atual = posicoes.get(t, 0)
        alvo_valor = pesos.get(t, 0.0) * patrimonio
        alvo_qtd = math.floor(alvo_valor / precos[t] / lote) * lote
        delta = alvo_qtd - atual
        if delta:
            ordens.append({"ticker": t, "lado": "VENDA" if delta < 0 else "COMPRA",
                           "quantidade": abs(delta), "preco": round(float(precos[t]), 2),
                           "valor": round(abs(delta) * float(precos[t]), 2)})
    df = pd.DataFrame(ordens, columns=["ticker", "lado", "quantidade", "preco", "valor"])
    return df.sort_values(["lado", "ticker"], ascending=[False, True], ignore_index=True)
