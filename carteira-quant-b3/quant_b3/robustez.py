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
          progresso=print) -> dict[tuple[str, str], pd.DataFrame]:
    """Roda o backtest de cada configuração.

    Devolve, por configuração, um DataFrame com `pat` (patrimônio) e `lat`
    (IR que seria pago vendendo tudo naquela data).
    """
    fatores = calcular_fatores(precos, volume, cfg)  # janelas iguais em todas: calcula uma vez
    cfgs = configuracoes(cfg)
    curvas = {}
    for i, (chave, cfg_c) in enumerate(cfgs.items(), 1):
        res = backtest.executar(precos, volume, cfg_c, cdi, fatores=fatores)
        curvas[chave] = pd.DataFrame({"pat": res.patrimonio,
                                      "lat": res.imposto_latente.reindex(res.patrimonio.index).fillna(0.0)})
        if i % 10 == 0 or i == len(cfgs):
            progresso(f"  {i}/{len(cfgs)} configurações")
    return curvas


def _cagr(serie: pd.Series) -> float:
    return metricas.cagr(serie)


def _cagr_liquido(curva: pd.DataFrame) -> float:
    """CAGR do trecho vendendo tudo no fim (desconta o IR latente da última data)."""
    return metricas.cagr(curva["pat"], curva["lat"].iloc[-1])


def _retornos_anuais(serie: pd.Series) -> pd.Series:
    fim_ano = serie.groupby(serie.index.year).last()
    inicio = pd.Series([serie.iloc[0]], index=[fim_ano.index[0] - 1])
    return pd.concat([inicio, fim_ano]).pct_change().dropna()


def grade(curvas: dict[tuple[str, str], pd.DataFrame], cdi: pd.Series,
          benchmark: pd.Series | None, corte: pd.Timestamp) -> pd.DataFrame:
    """Métricas por configuração: calibração, validação e consistência anual."""
    linhas = []
    for (linha, mistura), curva in curvas.items():
        pat = curva["pat"]
        reg = {"linha": linha, "mistura": mistura,
               "sharpe_calib": metricas.resumo(pat.loc[:corte], cdi)["sharpe"],
               "cagr_calib": _cagr_liquido(curva.loc[:corte]),
               "cagr_valid": _cagr_liquido(curva.loc[corte:]),
               "cagr_total": _cagr_liquido(curva), "queda_max": metricas.drawdown(pat).min()}
        if benchmark is not None:
            b = benchmark.reindex(pat.index).ffill()
            ra, rb = _retornos_anuais(pat), _retornos_anuais(b)
            reg["anos_acima_bench"] = (ra > rb.reindex(ra.index)).mean()
        linhas.append(reg)
    return pd.DataFrame(linhas)


def walk_forward(curvas: dict[tuple[str, str], pd.DataFrame], cdi: pd.Series,
                 benchmark: pd.Series | None, cortes=CORTES, aliquota_ref: float = 0.0,
                 peso_igual: pd.DataFrame | None = None) -> pd.DataFrame:
    """Para cada corte: escolhe a melhor (Sharpe) antes dele e mede o depois.

    CAGRs "depois" consideram vender tudo no fim pagando IR (`aliquota_ref` nas referências).
    `peso_igual`: curva (pat, lat) da carteira com todas as ações em peso igual.
    """
    linhas = []
    for corte in map(pd.Timestamp, cortes):
        amostra = next(iter(curvas.values()))
        if corte <= amostra.index[0] or corte >= amostra.index[-1]:
            continue
        sharpe = {k: metricas.resumo(c["pat"].loc[:corte], cdi)["sharpe"] for k, c in curvas.items()}
        escolhida = max(sharpe, key=lambda k: -float("inf") if pd.isna(sharpe[k]) else sharpe[k])
        depois = pd.Series({k: _cagr_liquido(c.loc[corte:]) for k, c in curvas.items()})
        reg = {
            "corte": corte, "escolhida": f"{escolhida[0]} | {escolhida[1]}",
            "cagr_escolhida": depois[escolhida],
            "posicao": f"{int(depois.rank(ascending=False)[escolhida])}/{len(depois)}",
            "cagr_mediana": depois.median(),
            "cdi": metricas.cagr_vendendo_no_fim((1 + cdi.loc[corte:]).cumprod(), aliquota_ref),
        }
        if benchmark is not None:
            reg["cagr_bench"] = metricas.cagr_vendendo_no_fim(benchmark.loc[corte:], aliquota_ref)
        if peso_igual is not None:
            reg["cagr_peso_igual"] = _cagr_liquido(peso_igual.loc[corte:])
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
    tem_pi = "cagr_peso_igual" in wf
    cab = (f"{'Corte':<9}{'Escolhida (melhor Sharpe antes do corte)':<{larg_esc}}"
           f"{'CAGR depois':>12}{'Posição':>9}{'Mediana':>9}"
           + (f"{'Peso igual':>12}" if tem_pi else "") + f"{nome_bench:>9}{'CDI':>8}")
    linhas = [cab]
    for _, r in wf.iterrows():
        linhas.append(f"{r['corte']:%m/%Y}  {r['escolhida']:<{larg_esc}}{pct(r['cagr_escolhida']):>12}"
                      f"{r['posicao']:>9}{pct(r['cagr_mediana']):>9}"
                      + (f"{pct(r['cagr_peso_igual']):>12}" if tem_pi else "")
                      + f"{pct(r.get('cagr_bench')):>9}{pct(r['cdi']):>8}")
    return "\n".join(linhas)
