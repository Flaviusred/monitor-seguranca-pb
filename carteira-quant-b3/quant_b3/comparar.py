"""Compara variações da estratégia em dois períodos: calibração e validação."""
from __future__ import annotations

import copy

import pandas as pd

from . import backtest, metricas

# Cada cenário altera o config.toml base. Chaves: seção -> {parâmetro: valor}.
CENARIOS: dict[str, dict] = {
    "Base (config.toml)": {},
    "Só momentum": {"fatores": {"peso_volatilidade": 0.0}},
    "Só baixa volatilidade": {"fatores": {"peso_momentum": 0.0}},
    "Trimestral": {"carteira": {"frequencia_meses": 3}},
    "Faixa top 20": {"carteira": {"faixa_manutencao": 20}},
    "Trimestral + faixa 20": {"carteira": {"frequencia_meses": 3, "faixa_manutencao": 20}},
    "Filtro tendência": {"tendencia": {"ativo": True}},
    "Tendência + faixa 20": {"tendencia": {"ativo": True}, "carteira": {"faixa_manutencao": 20}},
    "Tendência + trim. + faixa 20": {"tendencia": {"ativo": True},
                                     "carteira": {"frequencia_meses": 3, "faixa_manutencao": 20}},
    "20 ativos + faixa 30": {"carteira": {"num_ativos": 20, "faixa_manutencao": 30}},
}


def aplicar(cfg: dict, alteracoes: dict) -> dict:
    novo = copy.deepcopy(cfg)
    for secao, valores in alteracoes.items():
        novo.setdefault(secao, {}).update(valores)
    return novo


def _linha(patrimonio: pd.Series, cdi: pd.Series, corte: pd.Timestamp) -> dict[str, float]:
    linha = {}
    for sufixo, trecho in (("calib", patrimonio.loc[:corte]), ("valid", patrimonio.loc[corte:])):
        if len(trecho) < 20:
            continue
        m = metricas.resumo(trecho, cdi)
        linha[f"cagr_{sufixo}"] = m["cagr"]
        linha[f"sharpe_{sufixo}"] = m["sharpe"]
        linha[f"dd_{sufixo}"] = m["max_drawdown"]
    return linha


def rodar(precos: pd.DataFrame, volume: pd.DataFrame, cfg: dict, cdi: pd.Series,
          benchmark: pd.Series | None, corte: pd.Timestamp,
          nome_benchmark: str = "Benchmark") -> pd.DataFrame:
    linhas = {}
    for nome, alteracoes in CENARIOS.items():
        cfg_c = aplicar(cfg, alteracoes)
        if cfg_c.get("tendencia", {}).get("ativo") and benchmark is None:
            continue
        res = backtest.executar(precos, volume, cfg_c, cdi, benchmark)
        pat = res.patrimonio
        anos = (pat.index[-1] - pat.index[0]).days / 365.25
        r = res.rebalanceamentos
        linhas[nome] = {
            **_linha(pat, cdi, corte),
            "giro_anual": r["giro"].sum() / anos,
            "custo_anual": (r["custo"] / pat.reindex(r["data_execucao"]).values).sum() / anos,
            "meses_caixa": r["em_caixa"].mean(),
        }
        print(f"  {nome}: ok")

    inicio = min(pd.Timestamp(cfg["backtest"]["inicio"]), precos.index[-1])
    ref = {}
    if benchmark is not None:
        b = benchmark.loc[inicio:].dropna()
        ref[nome_benchmark] = b / b.iloc[0]
    fator_cdi = (1 + cdi.loc[inicio:]).cumprod()
    ref["CDI"] = fator_cdi
    for nome, serie in ref.items():
        linhas[nome] = _linha(serie, cdi, corte)
    return pd.DataFrame(linhas).T


def formatar(tabela: pd.DataFrame, corte: pd.Timestamp, fim: pd.Timestamp) -> str:
    pct = lambda v: "" if pd.isna(v) else f"{v:6.1%}"  # noqa: E731
    num = lambda v: "" if pd.isna(v) else f"{v:5.2f}"  # noqa: E731
    colunas = [
        ("cagr_calib", "CAGR", pct), ("sharpe_calib", "Sharpe", num), ("dd_calib", "Queda máx", pct),
        ("cagr_valid", "CAGR", pct), ("sharpe_valid", "Sharpe", num), ("dd_valid", "Queda máx", pct),
        ("giro_anual", "Giro/ano", lambda v: "" if pd.isna(v) else f"{v:5.0%}"),
        ("custo_anual", "Custo/ano", lambda v: "" if pd.isna(v) else f"{v:5.2%}"),
        ("meses_caixa", "Em caixa", lambda v: "" if pd.isna(v) else f"{v:4.0%}"),
    ]
    largura_nome = max(len(n) for n in tabela.index) + 2
    larg = 10
    calib = f"Calibração (até {corte:%m/%Y})"
    valid = f"Validação ({corte:%m/%Y} a {fim:%m/%Y})"
    linhas = [" " * largura_nome + f"{calib:^{3 * larg}}" + f"{valid:^{3 * larg}}",
              " " * largura_nome + "".join(f"{rot:>{larg}}" for _, rot, _ in colunas)]
    for nome, linha in tabela.iterrows():
        linhas.append(f"{nome:<{largura_nome}}" + "".join(
            f"{fmt(linha.get(chave)):>{larg}}" for chave, _, fmt in colunas))
    return "\n".join(linhas)
