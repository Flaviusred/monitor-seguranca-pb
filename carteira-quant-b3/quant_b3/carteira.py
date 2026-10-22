"""Seleção de ativos, pesos-alvo e geração de ordens de rebalanceamento."""
from __future__ import annotations

import math
from collections.abc import Callable, Iterable

import pandas as pd


def pesos_alvo(ranking: pd.DataFrame, num_ativos: int, ponderacao: str = "igual",
               atuais: Iterable[str] = (), faixa: int | None = None,
               grupo_de: Callable[[str], str] | None = None,
               setor_de: Callable[[str], str] | None = None,
               max_por_setor: int = 0) -> pd.Series:
    """Escolhe `num_ativos` ações do ranking e define os pesos (soma = 1).

    Com `faixa`, uma ação já em carteira (`atuais`) só sai se cair para fora das
    `faixa` melhores; as vagas restantes vão para as melhores do ranking. Isso
    reduz o giro (e os custos) em relação a sempre pegar o top `num_ativos`.

    `grupo_de` (ticker -> empresa) deixa só uma ação por empresa no ranking.
    `max_por_setor` (> 0) limita quantas ações de um mesmo setor (`setor_de`) entram.
    """
    faixa = max(faixa or num_ativos, num_ativos)
    atuais = set(atuais)
    if grupo_de is not None:
        ranking = uma_por_grupo(ranking, grupo_de, atuais, faixa)
    na_faixa = set(ranking.index[:faixa])
    mantidos = [t for t in ranking.index if t in atuais and t in na_faixa]
    candidatos = mantidos + [t for t in ranking.index if t not in set(mantidos)]

    escolhidos, por_setor = [], {}
    for t in candidatos:
        if len(escolhidos) == num_ativos:
            break
        if max_por_setor and setor_de is not None:
            setor = setor_de(t)
            if por_setor.get(setor, 0) >= max_por_setor:
                continue
            por_setor[setor] = por_setor.get(setor, 0) + 1
        escolhidos.append(t)
    escolhidos = ranking.loc[[t for t in ranking.index if t in set(escolhidos)]]
    if escolhidos.empty:
        return pd.Series(dtype=float)
    if ponderacao == "igual":
        pesos = pd.Series(1.0, index=escolhidos.index)
    elif ponderacao == "inv_vol":
        pesos = 1.0 / escolhidos["volatilidade"]
    else:
        raise ValueError(f"Ponderação desconhecida: {ponderacao!r}")
    return pesos / pesos.sum()


def uma_por_grupo(ranking: pd.DataFrame, grupo_de: Callable[[str], str],
                  atuais: set[str] = frozenset(), faixa: int | None = None) -> pd.DataFrame:
    """Mantém só a melhor ação de cada empresa/grupo no ranking.

    Se outra ação do grupo já está em carteira e segue entre as `faixa` melhores,
    ela é mantida no lugar da melhor (evita trocar PETR3 por PETR4 à toa).
    """
    na_faixa = set(ranking.index[:faixa]) if faixa else set(ranking.index)
    escolhido: dict[str, str] = {}
    for t in ranking.index:
        g = grupo_de(t)
        if g not in escolhido:
            escolhido[g] = t
        elif t in atuais and t in na_faixa and escolhido[g] not in atuais:
            escolhido[g] = t
    manter = set(escolhido.values())
    return ranking[[t in manter for t in ranking.index]]


def opcoes_selecao(cfg: dict) -> dict:
    """Argumentos de `pesos_alvo` vindos do config (faixa, empresas e setores)."""
    cart = cfg["carteira"]
    opcoes = {"faixa": cart.get("faixa_manutencao")}
    if cart.get("uma_por_empresa"):
        grupos = {}
        for lista in cfg.get("empresas", {}).get("mesmo_grupo", []):
            for t in lista:
                grupos[t] = lista[0]
        # Sem grupo explícito, a empresa é a raiz do ticker (PETR3 e PETR4 -> PETR).
        opcoes["grupo_de"] = lambda t: grupos.get(t, t[:4])
    if cart.get("max_por_setor"):
        setores = {t: setor for setor, lista in cfg.get("setores", {}).items() for t in lista}
        # Ação sem setor definido conta como setor próprio (não é limitada).
        opcoes["setor_de"] = lambda t: setores.get(t, t)
        opcoes["max_por_setor"] = cart["max_por_setor"]
    return opcoes


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
