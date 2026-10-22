"""Linha de comando.

    python -m quant_b3 backtest                  # roda o backtest
    python -m quant_b3 comparar                  # compara variações da estratégia
    python -m quant_b3 robustez                  # grade de parâmetros e walk-forward
    python -m quant_b3 carteira                  # ranking e ordens do mês (simulação)
    python -m quant_b3 carteira --registrar      # grava as ordens no paper trading
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from . import backtest, cdi, comparar, dados, metricas, robustez
from .carteira import gerar_ordens, opcoes_selecao, pesos_alvo
from .fatores import abaixo_da_media, pontuar
from .paper import Carteira

RAIZ = Path(__file__).resolve().parent.parent
ISENCAO_MENSAL = 20_000.0


def carregar_config(caminho: Path) -> dict:
    with open(caminho, "rb") as f:
        return tomllib.load(f)


def carregar_dados(cfg: dict, args, inicio: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    if args.precos_csv:
        precos = dados.ler_csv(args.precos_csv)
        volume = dados.ler_csv(args.volume_csv) if args.volume_csv else None
        if volume is None:
            print("Aviso: sem CSV de volume; filtro de liquidez desativado.")
            volume = pd.DataFrame(float("inf"), index=precos.index, columns=precos.columns)
        return precos, volume
    tickers = cfg["universo"]["tickers"] + [cfg["universo"]["benchmark"]]
    return dados.baixar_yahoo(tickers, inicio, usar_cache=not args.sem_cache)


def preparar(cfg: dict, args) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series | None, pd.Series]:
    """Dados do backtest: preços e volume das ações, benchmark e CDI por pregão."""
    # Baixa ~2 anos antes do início para aquecer o momentum de 12 meses.
    inicio_dados = (pd.Timestamp(cfg["backtest"]["inicio"]) - pd.DateOffset(years=2)).date().isoformat()
    precos, volume = carregar_dados(cfg, args, inicio_dados)
    bench = cfg["universo"]["benchmark"]
    precos_bench = precos.pop(bench) if bench in precos else None
    volume = volume.drop(columns=[bench], errors="ignore")
    serie_cdi = cdi.baixar_cdi(inicio_dados, usar_cache=not args.sem_cache)
    if serie_cdi is None:
        print(f"Usando CDI fixo de {cfg['backtest']['taxa_livre_risco_anual']:.1%} ao ano.")
    ret_cdi = cdi.retornos_por_pregao(serie_cdi, precos.index, cfg["backtest"]["taxa_livre_risco_anual"])
    return precos, volume, precos_bench, ret_cdi


def fmt_pct(x: float) -> str:
    return f"{x:7.2%}"


def cmd_backtest(cfg: dict, args) -> None:
    precos, volume, precos_bench, ret_cdi = preparar(cfg, args)
    bench = cfg["universo"]["benchmark"]
    res = backtest.executar(precos, volume, cfg, ret_cdi, precos_bench)
    if res.patrimonio.empty:
        raise SystemExit("Histórico insuficiente para o backtest.")

    vazios = (res.rebalanceamentos["ativos"] == "").sum()
    if vazios:
        print(f"Aviso: em {vazios} rebalanceamento(s) nenhuma ação passou nos filtros "
              "(a carteira ficou em caixa). Revise [filtros] no config.")

    capital = cfg["backtest"]["capital_inicial"]
    aliquota = comparar.aliquota_ir(cfg)
    curvas = {"Estratégia": res.patrimonio}
    if precos_bench is not None:
        b = precos_bench.loc[res.patrimonio.index[0]:].dropna()
        curvas[bench] = b / b.iloc[0] * capital
    curvas["CDI"] = (1 + ret_cdi.loc[res.patrimonio.index[0]:]).cumprod() * capital
    tabela = {nome: metricas.resumo(c, ret_cdi) for nome, c in curvas.items()}
    # Vendendo tudo no fim: a estratégia paga o IR latente; BOVA11 e CDI pagam sobre o ganho.
    tabela["Estratégia"]["cagr_liquido"] = metricas.cagr(res.patrimonio, res.imposto_latente.iloc[-1])
    for nome in list(curvas)[1:]:
        tabela[nome]["cagr_liquido"] = metricas.cagr_vendendo_no_fim(curvas[nome], aliquota)

    print(f"\nPeríodo: {res.patrimonio.index[0]:%d/%m/%Y} a {res.patrimonio.index[-1]:%d/%m/%Y}"
          f" | {len(res.rebalanceamentos)} rebalanceamentos")
    print(f"Patrimônio final: R$ {res.patrimonio.iloc[-1]:,.2f}"
          f" | custos totais: R$ {res.rebalanceamentos['custo'].sum():,.2f}"
          f" | giro médio: {res.rebalanceamentos['giro'].mean():.1%}"
          f" | em caixa: {res.rebalanceamentos['em_caixa'].mean():.0%} dos rebalanceamentos\n")
    print(f"{'':16}" + "".join(f"{nome:>14}" for nome in tabela))
    for chave, rotulo in [("retorno_total", "Retorno total"), ("cagr", "CAGR"),
                          ("volatilidade", "Volatilidade"), ("max_drawdown", "Máx. drawdown"),
                          ("meses_positivos", "Meses positivos")]:
        print(f"{rotulo:16}" + "".join(f"{fmt_pct(m[chave]):>14}" for m in tabela.values()))
    print(f"{'Sharpe':16}" + "".join(f"{m['sharpe']:>14.2f}" for m in tabela.values()))
    if aliquota:
        print(f"{'CAGR após IR*':16}" + "".join(f"{fmt_pct(m['cagr_liquido']):>14}" for m in tabela.values()))
        ir = res.impostos
        pago = ir["imposto"].sum() if len(ir) else 0.0
        meses_tributados = int((~ir["isento"] & (ir["imposto"] > 0)).sum()) if len(ir) else 0
        print(f"\nIR pago pela estratégia: R$ {pago:,.2f} em {meses_tributados} meses"
              f" | IR se vendesse tudo hoje: R$ {res.imposto_latente.iloc[-1]:,.2f}"
              f" | prejuízo a compensar: R$ {ir['prejuizo_acumulado'].iloc[-1] if len(ir) else 0:,.2f}")
        print("* CAGR de quem vende tudo no fim do período, pagando o IR devido. A estratégia já\n"
              "  paga IR mês a mês ao longo do caminho; BOVA11 e CDI só pagam na venda.")

    saida = RAIZ / "resultados"
    saida.mkdir(exist_ok=True)
    res.patrimonio.to_csv(saida / "patrimonio.csv")
    res.rebalanceamentos.to_csv(saida / "rebalanceamentos.csv", index=False)
    if len(res.impostos):
        res.impostos.to_csv(saida / "impostos.csv", index=False)
    print(f"\nArquivos salvos em {saida}/")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True,
                                       gridspec_kw={"height_ratios": [3, 1]})
        ax1.plot(res.patrimonio, label="Estratégia")
        if precos_bench is not None:
            ax1.plot(b / b.iloc[0] * capital, label=bench, alpha=0.7)
        ax1.set_ylabel("Patrimônio (R$)")
        ax1.legend()
        ax2.fill_between(res.patrimonio.index, metricas.drawdown(res.patrimonio), 0, alpha=0.4)
        ax2.set_ylabel("Drawdown")
        fig.tight_layout()
        fig.savefig(saida / "patrimonio.png", dpi=110)
    except ImportError:
        pass


def cmd_comparar(cfg: dict, args) -> None:
    precos, volume, precos_bench, ret_cdi = preparar(cfg, args)
    corte = pd.Timestamp(args.corte)
    print(f"\nRodando {len(comparar.CENARIOS)} cenários...")
    tabela = comparar.rodar(precos, volume, cfg, ret_cdi, precos_bench, corte,
                            cfg["universo"]["benchmark"])
    print("\n" + comparar.formatar(tabela, corte, precos.index[-1]))
    print("\nEscolha pela CALIBRAÇÃO; a VALIDAÇÃO mostra se a vantagem se mantém em dados\n"
          "que não influenciaram a escolha. Giro/ano conta compras + vendas.")
    saida = RAIZ / "resultados"
    saida.mkdir(exist_ok=True)
    tabela.to_csv(saida / "comparacao.csv")
    print(f"Tabela salva em {saida / 'comparacao.csv'}")


def cmd_robustez(cfg: dict, args) -> None:
    precos, volume, precos_bench, ret_cdi = preparar(cfg, args)
    bench = cfg["universo"]["benchmark"]
    corte = pd.Timestamp(args.corte)
    print("\nRodando a grade de parâmetros...")
    curvas = robustez.rodar(precos, volume, cfg, ret_cdi)
    g = robustez.grade(curvas, ret_cdi, precos_bench, corte)
    aliquota = comparar.aliquota_ir(cfg)
    # Referência sem ranking: todas as ações com peso igual.
    res_pi = backtest.executar(precos, volume, comparar.aplicar(cfg, comparar.CENARIOS[comparar.PESO_IGUAL]),
                               ret_cdi)
    peso_igual = pd.DataFrame({"pat": res_pi.patrimonio,
                               "lat": res_pi.imposto_latente.reindex(res_pi.patrimonio.index).fillna(0.0)})
    wf = robustez.walk_forward(curvas, ret_cdi, precos_bench, aliquota_ref=aliquota, peso_igual=peso_igual)

    pct = lambda v: "" if pd.isna(v) else f"{v:.1%}"  # noqa: E731
    num = lambda v: "" if pd.isna(v) else f"{v:.2f}"  # noqa: E731
    fim = precos.index[-1]
    secoes = [
        (f"1) Sharpe na calibração (até {corte:%m/%Y})", "sharpe_calib", num),
        (f"2) CAGR na validação ({corte:%m/%Y} a {fim:%m/%Y})", "cagr_valid", pct),
        ("3) Queda máxima (período inteiro)", "queda_max", pct),
    ]
    if "anos_acima_bench" in g:
        secoes.append((f"4) % dos anos acima do {bench} (período inteiro)", "anos_acima_bench",
                       lambda v: "" if pd.isna(v) else f"{v:.0%}"))
    for titulo, coluna, fmt in secoes:
        print(f"\n{titulo}\n" + robustez.tabela_cruzada(g, coluna, fmt))

    if precos_bench is not None:
        b = precos_bench.loc[corte:].dropna()
        print(f"\nReferência na validação: {bench} {pct(metricas.cagr_vendendo_no_fim(b, aliquota))} | "
              f"CDI {pct(metricas.cagr_vendendo_no_fim((1 + ret_cdi.loc[corte:]).cumprod(), aliquota))} | "
              f"Peso igual (todas as ações) {pct(robustez._cagr_liquido(peso_igual.loc[corte:]))}")
    if aliquota:
        print("CAGRs após IR, vendendo tudo no fim de cada período (estratégias pagam também o IR\n"
              "mensal ao longo do caminho). O item 4 compara retornos anuais da estratégia já com IR\n"
              f"mensal pago contra o {bench} sem IR (comparação conservadora).")
    print("\n5) Walk-forward: escolher a melhor configuração com os dados de antes de cada corte\n"
          "   e medir o que ela rendeu depois (posição = colocação entre todas as configurações)\n")
    print(robustez.formatar_walk_forward(wf, bench))
    print("\nComo ler: vizinhos com números parecidos (sem uma célula isolada muito melhor) indicam\n"
          "robustez. No walk-forward, a escolhida deveria ficar acima da mediana e do benchmark\n"
          "na maioria dos cortes; se não ficar, escolher pelo passado não está funcionando.\n"
          "Se a carteira de peso igual render tanto quanto as configurações, o ranking por\n"
          "fatores não está acrescentando nada além de diversificar em peso igual.")

    saida = RAIZ / "resultados"
    saida.mkdir(exist_ok=True)
    g.to_csv(saida / "robustez_grade.csv", index=False)
    wf.to_csv(saida / "robustez_walkforward.csv", index=False)
    print(f"\nTabelas salvas em {saida}/robustez_*.csv")


def cmd_carteira(cfg: dict, args) -> None:
    inicio = (date.today() - timedelta(days=550)).isoformat()
    precos, volume = carregar_dados(cfg, args, inicio)
    bench = cfg["universo"]["benchmark"]
    precos_bench = precos.pop(bench) if bench in precos else None
    volume = volume.drop(columns=[bench], errors="ignore")
    data = precos.index[-1]

    paper = Carteira(Path(args.paper_dir))
    if not paper.existe():
        paper.iniciar(args.capital)
        print(f"\nCarteira de simulação criada com R$ {args.capital:,.2f} em {paper.diretorio}/")

    ranking = pontuar(precos, volume, data, cfg)
    cart = cfg["carteira"]
    pesos = pesos_alvo(ranking, cart["num_ativos"], cart["ponderacao"],
                       paper.posicoes, **opcoes_selecao(cfg))
    tendencia = cfg.get("tendencia", {})
    if tendencia.get("ativo"):
        if precos_bench is None:
            raise SystemExit(f"Filtro de tendência ativo, mas sem preços de {bench}.")
        if abaixo_da_media(precos_bench, data, tendencia.get("media_dias", 200)):
            print(f"\nFiltro de tendência: {bench} abaixo da média de "
                  f"{tendencia.get('media_dias', 200)} dias. Carteira vai para o caixa (CDI).")
            pesos = pd.Series(dtype=float)

    print(f"\nRanking em {data:%d/%m/%Y} ({len(ranking)} ações elegíveis):\n")
    vis = ranking.head(max(20, cart["num_ativos"])).copy()
    vis["peso_alvo"] = pesos.reindex(vis.index).fillna(0)
    print(vis.to_string(formatters={
        "preco": "{:,.2f}".format, "momentum": "{:+.1%}".format, "volatilidade": "{:.1%}".format,
        "liquidez": lambda v: f"{v / 1e6:,.0f} mi", "pontuacao": "{:+.2f}".format,
        "peso_alvo": lambda v: f"{v:.1%}" if v else "-"}))

    ultimos = precos.ffill().iloc[-1]
    ordens = gerar_ordens(pesos, paper.posicoes, paper.caixa, ultimos, cart["lote_minimo"])

    print(f"\nPatrimônio simulado: R$ {paper.patrimonio(ultimos):,.2f}"
          f" (caixa R$ {paper.caixa:,.2f})")
    if ordens.empty:
        print("Nenhuma ordem necessária.")
        return
    print("\nOrdens de rebalanceamento:\n")
    print(ordens.to_string(index=False))

    vendas = paper.vendas_no_mes(data) + ordens.loc[ordens["lado"] == "VENDA", "valor"].sum()
    if vendas > ISENCAO_MENSAL:
        print(f"\nAtenção (IR): vendas de ações no mês somariam R$ {vendas:,.2f}, acima da isenção "
              f"de R$ {ISENCAO_MENSAL:,.0f}. Lucros dessas vendas são tributados em 15% (DARF).")

    if args.registrar:
        paper.registrar(ordens, data)
        print(f"\nOrdens registradas na simulação ({paper.diretorio}/operacoes.csv).")
    else:
        print("\nSimulação apenas. Use --registrar para gravar no paper trading.")


def main() -> None:
    p = argparse.ArgumentParser(prog="quant_b3", description="Carteira quantitativa B3")
    p.add_argument("--config", default=RAIZ / "config.toml", type=Path)
    p.add_argument("--precos-csv", help="CSV de preços ajustados (em vez do Yahoo)")
    p.add_argument("--volume-csv", help="CSV de volume financeiro diário (R$)")
    p.add_argument("--sem-cache", action="store_true", help="força novo download")
    sub = p.add_subparsers(dest="comando", required=True)
    sub.add_parser("backtest", help="roda o backtest histórico")
    pcmp = sub.add_parser("comparar", help="compara variações em calibração e validação")
    pcmp.add_argument("--corte", default="2021-01-01",
                      help="data que separa calibração e validação (padrão 2021-01-01)")
    prob = sub.add_parser("robustez", help="grade de parâmetros vizinhos e walk-forward")
    prob.add_argument("--corte", default="2021-01-01",
                      help="data que separa calibração e validação na grade (padrão 2021-01-01)")
    pc = sub.add_parser("carteira", help="ranking atual e ordens (paper trading)")
    pc.add_argument("--registrar", action="store_true", help="grava as ordens na simulação")
    pc.add_argument("--capital", type=float, default=100_000, help="capital inicial da simulação")
    pc.add_argument("--paper-dir", default=str(RAIZ / "paper"))
    args = p.parse_args()

    cfg = carregar_config(args.config)
    {"backtest": cmd_backtest, "comparar": cmd_comparar, "robustez": cmd_robustez,
     "carteira": cmd_carteira}[args.comando](cfg, args)


if __name__ == "__main__":
    main()
