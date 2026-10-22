"""Testes de robustez: grade de parâmetros vizinhos e seleção walk-forward.

Uma configuração confiável tem vizinhos (parâmetros parecidos) com resultados
parecidos. E o método "escolher a melhor no passado" só tem valor se a escolhida
continuar boa no período seguinte, para vários pontos de corte.
"""
from __future__ import annotations

import pandas as pd

from . import backtest, metricas
from .comparar import aplicar
from .fatores import calcular_fatores

NUM_ATIVOS = [10, 15, 20, 25, 30]
FAIXA_EXTRA = [0, 10]          # faixa de manutenção = num_ativos + extra
MISTURAS = {                   # pesos dos fatores
    "Mom 1 / Vol 0,5": {"peso_momentum": 1.0, "peso_volatilidade": 0.5},
    "Mom 1 / Vol 1": {"peso_momentum": 1.0, "peso_volatilidade": 1.0},
    "Mom 1 / Vol 2": {"peso_momentum": 1.0, "peso_volatilidade": 2.0},
    "Só baixa vol": {"peso_momentum": 0.0, "peso_volatilidade": 1.0},
}
CORTES = ["2019-01-01", "2020-01-01", "2021-01-01", "2022-01-01"]


def configuracoes(cfg: dict) -> dict[tuple[str, str], dict]:
    """(linha, mistura) -> config. A linha descreve tamanho da carteira e faixa."""
    saida = {}
    for n in NUM_ATIVOS:
        for extra in FAIXA_EXTRA:
            linha = f"{n} ativos, faixa {n + extra}"
            for nome, pesos in MISTURAS.items():
                saida[(linha, nome)] = aplicar(cfg, {
                    "carteira": {"num_ativos": n, "faixa_manutencao": n + extra, "frequencia_meses": 1},
                    "fatores": pesos,
                    "tendencia": {"ativo": False},
                })
    return saida


def rodar(precos: pd.DataFrame, volume: pd.DataFrame, cfg: dict, cdi: pd.Series,
          progresso=print) -> dict[tuple[str, str], pd.Series]:
    """Roda o backtest de cada configuração e devolve as curvas de patrimônio."""
    fatores = calcular_fatores(precos, volume, cfg)  # janelas iguais em todas: calcula uma vez
    cfgs = configuracoes(cfg)
    curvas = {}
    for i, (chave, cfg_c) in enumerate(cfgs.items(), 1):
        curvas[chave] = backtest.executar(precos, volume, cfg_c, cdi, fatores=fatores).patrimonio
        if i % 10 == 0 or i == len(cfgs):
            progresso(f"  {i}/{len(cfgs)} configurações")
    return curvas


def _cagr(serie: pd.Series) -> float:
    serie = serie.dropna()
    anos = (serie.index[-1] - serie.index[0]).days / 365.25
    return (serie.iloc[-1] / serie.iloc[0]) ** (1 / anos) - 1 if anos > 0.5 else float("nan")


def _retornos_anuais(serie: pd.Series) -> pd.Series:
    fim_ano = serie.groupby(serie.index.year).last()
    inicio = pd.Series([serie.iloc[0]], index=[fim_ano.index[0] - 1])
    return pd.concat([inicio, fim_ano]).pct_change().dropna()


def grade(curvas: dict[tuple[str, str], pd.Series], cdi: pd.Series,
          benchmark: pd.Series | None, corte: pd.Timestamp) -> pd.DataFrame:
    """Métricas por configuração: calibração, validação e consistência anual."""
    linhas = []
    for (linha, mistura), pat in curvas.items():
        calib, valid = pat.loc[:corte], pat.loc[corte:]
        reg = {"linha": linha, "mistura": mistura,
               "sharpe_calib": metricas.resumo(calib, cdi)["sharpe"],
               "cagr_calib": _cagr(calib), "cagr_valid": _cagr(valid),
               "cagr_total": _cagr(pat), "queda_max": metricas.drawdown(pat).min()}
        if benchmark is not None:
            b = benchmark.reindex(pat.index).ffill()
            ra, rb = _retornos_anuais(pat), _retornos_anuais(b)
            reg["anos_acima_bench"] = (ra > rb.reindex(ra.index)).mean()
        linhas.append(reg)
    return pd.DataFrame(linhas)


def walk_forward(curvas: dict[tuple[str, str], pd.Series], cdi: pd.Series,
                 benchmark: pd.Series | None, cortes=CORTES) -> pd.DataFrame:
    """Para cada corte: escolhe a melhor (Sharpe) antes dele e mede o depois."""
    linhas = []
    for corte in map(pd.Timestamp, cortes):
        amostra = next(iter(curvas.values()))
        if corte <= amostra.index[0] or corte >= amostra.index[-1]:
            continue
        sharpe = {k: metricas.resumo(p.loc[:corte], cdi)["sharpe"] for k, p in curvas.items()}
        escolhida = max(sharpe, key=lambda k: -float("inf") if pd.isna(sharpe[k]) else sharpe[k])
        depois = pd.Series({k: _cagr(p.loc[corte:]) for k, p in curvas.items()})
        reg = {
            "corte": corte, "escolhida": f"{escolhida[0]} | {escolhida[1]}",
            "cagr_escolhida": depois[escolhida],
            "posicao": f"{int(depois.rank(ascending=False)[escolhida])}/{len(depois)}",
            "cagr_mediana": depois.median(),
            "cdi": _cagr((1 + cdi.loc[corte:]).cumprod()),
        }
        if benchmark is not None:
            reg["cagr_bench"] = _cagr(benchmark.loc[corte:])
        linhas.append(reg)
    return pd.DataFrame(linhas)


def tabela_cruzada(g: pd.DataFrame, valor: str, fmt) -> str:
    """Linhas = tamanho/faixa, colunas = mistura de fatores."""
    piv = g.pivot(index="linha", columns="mistura", values=valor)
    piv = piv.reindex(index=list(dict.fromkeys(g["linha"])), columns=list(MISTURAS))
    larg_linha = max(len(i) for i in piv.index) + 2
    larg = max(len(c) for c in piv.columns) + 2
    saida = [" " * larg_linha + "".join(f"{c:>{larg}}" for c in piv.columns)]
    for nome, linha in piv.iterrows():
        saida.append(f"{nome:<{larg_linha}}" + "".join(f"{fmt(v):>{larg}}" for v in linha))
    return "\n".join(saida)


def formatar_walk_forward(wf: pd.DataFrame, nome_bench: str) -> str:
    pct = lambda v: "" if pd.isna(v) else f"{v:.1%}"  # noqa: E731
    larg_esc = max([len("Escolhida (melhor Sharpe antes do corte)")] + [len(e) for e in wf["escolhida"]]) + 2
    cab = (f"{'Corte':<9}{'Escolhida (melhor Sharpe antes do corte)':<{larg_esc}}"
           f"{'CAGR depois':>12}{'Posição':>9}{'Mediana':>9}{nome_bench:>9}{'CDI':>8}")
    linhas = [cab]
    for _, r in wf.iterrows():
        linhas.append(f"{r['corte']:%m/%Y}  {r['escolhida']:<{larg_esc}}{pct(r['cagr_escolhida']):>12}"
                      f"{r['posicao']:>9}{pct(r['cagr_mediana']):>9}"
                      f"{pct(r.get('cagr_bench')):>9}{pct(r['cdi']):>8}")
    return "\n".join(linhas)
