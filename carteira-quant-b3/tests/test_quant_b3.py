import numpy as np
import pandas as pd
import pytest

from quant_b3 import backtest, metricas
from quant_b3.carteira import gerar_ordens, pesos_alvo
from quant_b3.fatores import momentum, pontuar
from quant_b3.paper import Carteira

CFG = {
    "filtros": {"liquidez_minima": 1_000_000, "preco_minimo": 1.0},
    "fatores": {"janela_momentum": 252, "pular_recente": 21, "janela_volatilidade": 63,
                "peso_momentum": 1.0, "peso_volatilidade": 0.5},
    "carteira": {"num_ativos": 3, "ponderacao": "igual", "lote_minimo": 1},
    "backtest": {"inicio": "2020-01-01", "capital_inicial": 100_000, "custo_bps": 15,
                 "atraso_execucao": 1, "taxa_livre_risco_anual": 0.0},
}


def dados_sinteticos(n_dias=900, n_ativos=8, semente=1):
    """Ativo i tem tendência crescente com i: A7 é o melhor, A0 o pior."""
    rng = np.random.default_rng(semente)
    datas = pd.bdate_range("2019-01-01", periods=n_dias)
    drift = np.linspace(-0.0005, 0.0010, n_ativos)
    ret = rng.normal(drift, 0.01, size=(n_dias, n_ativos))
    precos = pd.DataFrame(20 * np.exp(np.cumsum(ret, axis=0)), index=datas,
                          columns=[f"A{i}" for i in range(n_ativos)])
    volume = pd.DataFrame(5_000_000.0, index=datas, columns=precos.columns)
    return precos, volume


def test_momentum_12_1():
    precos = pd.DataFrame({"X": np.arange(1.0, 301.0)})
    m = momentum(precos, janela=252, pular=21).iloc[-1, 0]
    # preço em t-21 = 279, em t-252 = 48
    assert m == pytest.approx(279 / 48 - 1)


def test_pontuar_prefere_tendencia_e_aplica_filtros():
    precos, volume = dados_sinteticos()
    volume["A7"] = 10.0  # ilíquida: deve ser excluída
    ranking = pontuar(precos, volume, precos.index[-1], CFG)
    assert "A7" not in ranking.index
    assert ranking.index[0] == "A6"
    assert ranking["pontuacao"].is_monotonic_decreasing


def test_pontuar_sem_dados_futuros():
    precos, volume = dados_sinteticos()
    data = precos.index[600]
    antes = pontuar(precos, volume, data, CFG)
    alterado = precos.copy()
    alterado.iloc[601:] *= 3  # mexer no futuro não pode mudar o sinal
    depois = pontuar(alterado, volume, data, CFG)
    pd.testing.assert_frame_equal(antes, depois)


def test_pesos_inv_vol():
    ranking = pd.DataFrame({"volatilidade": [0.2, 0.4]}, index=["A", "B"])
    pesos = pesos_alvo(ranking, 2, "inv_vol")
    assert pesos["A"] == pytest.approx(2 / 3)
    assert pesos.sum() == pytest.approx(1)


def test_gerar_ordens_vende_saida_e_respeita_lote():
    pesos = pd.Series({"A": 0.5, "B": 0.5})
    precos = pd.Series({"A": 10.0, "B": 33.0, "C": 50.0})
    ordens = gerar_ordens(pesos, {"C": 100}, caixa=0.0, precos=precos, lote=100)
    o = ordens.set_index("ticker")
    assert ordens.iloc[0]["lado"] == "VENDA"
    assert o.loc["C", "quantidade"] == 100
    assert o.loc["A", "quantidade"] == 200   # 2.500 / 10 = 250 -> 200
    assert "B" not in o.index  # 2.500 / 33 = 75 -> 0 no lote padrão
    gasto = o.loc[o["lado"] == "COMPRA", "valor"].sum()
    assert gasto <= 5_000


def test_backtest_custos_reduzem_resultado():
    precos, volume = dados_sinteticos()
    sem_custo = backtest.executar(precos, volume, {**CFG, "backtest": {**CFG["backtest"], "custo_bps": 0}})
    com_custo = backtest.executar(precos, volume, CFG)
    assert com_custo.patrimonio.iloc[-1] < sem_custo.patrimonio.iloc[-1]
    assert com_custo.rebalanceamentos["custo"].sum() > 0
    # execução sempre depois do sinal
    r = com_custo.rebalanceamentos
    assert (r["data_execucao"] > r["data_sinal"]).all()
    assert (r["data_sinal"] >= pd.Timestamp(CFG["backtest"]["inicio"])).all()


def test_backtest_acao_deslistada_nao_e_comprada():
    precos, volume = dados_sinteticos()
    precos.loc[precos.index[500]:, "A6"] = np.nan
    res = backtest.executar(precos, volume, CFG)
    depois = [p for d, p in res.pesos.items() if d > precos.index[520]]
    assert depois and all("A6" not in p.index for p in depois)
    assert res.patrimonio.notna().all()


def test_metricas():
    datas = pd.bdate_range("2020-01-01", periods=253)
    pat = pd.Series(np.linspace(100, 110, 253), index=datas)
    pat.iloc[100] = 90  # queda pontual
    m = metricas.resumo(pat)
    assert m["retorno_total"] == pytest.approx(0.10)
    assert m["max_drawdown"] < -0.1


def test_paper_trading(tmp_path):
    cart = Carteira(tmp_path)
    cart.iniciar(10_000)
    precos = pd.Series({"A": 10.0, "B": 20.0})
    ordens = gerar_ordens(pd.Series({"A": 0.5, "B": 0.5}), cart.posicoes, cart.caixa, precos)
    cart.registrar(ordens, pd.Timestamp("2024-03-28"))

    recarregada = Carteira(tmp_path)
    assert recarregada.posicoes == {"A": 500, "B": 250}
    assert recarregada.caixa == pytest.approx(0)

    ordens = gerar_ordens(pd.Series({"B": 1.0}), recarregada.posicoes, recarregada.caixa, precos)
    recarregada.registrar(ordens, pd.Timestamp("2024-04-30"))
    assert recarregada.posicoes == {"B": 500}
    assert recarregada.vendas_no_mes(pd.Timestamp("2024-04-15")) == pytest.approx(5_000)


def test_download_tenta_de_novo_e_nao_guarda_cache_incompleto(tmp_path, monkeypatch):
    import sys
    import types
    from quant_b3 import dados

    datas = pd.bdate_range("2024-01-01", periods=5)
    falhas = {"B.SA": 1, "C.SA": 99}  # B falha 1 vez; C sempre falha
    chamadas = []

    def download(tickers, threads, **kw):
        chamadas.append((list(tickers), threads))
        ok = []
        for t in tickers:
            if falhas.get(t, 0) > 0:
                falhas[t] -= 1
            else:
                ok.append(t)
        if not ok:
            return pd.DataFrame()
        cols = pd.MultiIndex.from_product([["Close", "Volume"], ok])
        return pd.DataFrame(10.0, index=datas, columns=cols)

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(download=download))
    monkeypatch.setattr(dados, "CACHE_DIR", tmp_path)

    precos, volume = dados.baixar_yahoo(["A", "B", "C"], "2024-01-01")
    assert list(precos.columns) == ["A", "B"]
    assert precos.notna().all().all() and (volume == 100.0).all().all()
    assert chamadas[1] == (["B.SA", "C.SA"], False)  # 2ª tentativa, sem paralelismo
    assert not list(tmp_path.iterdir())  # C faltou: nada vai para o cache


def test_faixa_mantem_acao_que_caiu_pouco_no_ranking():
    ranking = pd.DataFrame({"volatilidade": 0.3}, index=list("ABCDEF"))
    # E está em carteira e é a 5ª: sem faixa sai; com faixa 5 fica no lugar de B.
    assert list(pesos_alvo(ranking, 2, atuais=["E"]).index) == ["A", "B"]
    assert list(pesos_alvo(ranking, 2, atuais=["E"], faixa=5).index) == ["A", "E"]
    assert list(pesos_alvo(ranking, 2, atuais=["F"], faixa=5).index) == ["A", "B"]


def test_faixa_reduz_giro_no_backtest():
    precos, volume = dados_sinteticos(n_ativos=15, semente=3)
    sem = backtest.executar(precos, volume, CFG)
    com = backtest.executar(precos, volume, {**CFG, "carteira": {**CFG["carteira"], "faixa_manutencao": 8}})
    assert com.rebalanceamentos["giro"].sum() < sem.rebalanceamentos["giro"].sum()


def test_trimestral_rebalanceia_a_cada_3_meses():
    precos, volume = dados_sinteticos()
    mensal = backtest.executar(precos, volume, CFG)
    trim = backtest.executar(precos, volume, {**CFG, "carteira": {**CFG["carteira"], "frequencia_meses": 3}})
    assert len(trim.rebalanceamentos) == -(-len(mensal.rebalanceamentos) // 3)


def test_filtro_tendencia_vai_para_caixa_que_rende_cdi():
    from quant_b3.cdi import retornos_por_pregao
    from quant_b3.fatores import abaixo_da_media

    precos, volume = dados_sinteticos()
    queda = pd.Series(np.linspace(100, 50, len(precos)), index=precos.index)  # sempre abaixo da média
    assert abaixo_da_media(queda, precos.index[-1], 200)
    cdi = retornos_por_pregao(None, precos.index, 0.10)
    cfg = {**CFG, "tendencia": {"ativo": True, "media_dias": 200}}
    res = backtest.executar(precos, volume, cfg, cdi, queda)
    assert res.rebalanceamentos["em_caixa"].all()
    pat = res.patrimonio
    anos = (pat.index[-1] - pat.index[0]).days / 365.25
    assert (pat.iloc[-1] / pat.iloc[0]) ** (1 / anos) - 1 == pytest.approx(0.10, abs=0.01)


def test_cdi_por_pregao_compoe_dias_sem_pregao():
    from quant_b3.cdi import retornos_por_pregao

    cdi = pd.Series(0.001, index=pd.bdate_range("2024-01-01", periods=10))
    pregoes = cdi.index[[0, 1, 4, 9]]  # pula dias (feriados na bolsa)
    ret = retornos_por_pregao(cdi, pregoes, 0.0)
    assert ret.iloc[0] == 0
    assert ret.iloc[2] == pytest.approx(1.001 ** 3 - 1)
    assert (1 + ret).prod() == pytest.approx(1.001 ** 9)


def test_baixar_cdi_em_partes(tmp_path, monkeypatch):
    import sys
    import types
    from quant_b3 import cdi as mod_cdi

    pedidos = []

    class Resp:
        def __init__(self, params):
            ini = pd.to_datetime(params["dataInicial"], format="%d/%m/%Y")
            fim = pd.to_datetime(params["dataFinal"], format="%d/%m/%Y")
            self.dados = [{"data": f"{d:%d/%m/%Y}", "valor": "0.040000"}
                          for d in pd.bdate_range(ini, fim)]

        def raise_for_status(self):
            pass

        def json(self):
            return self.dados

    def get(url, params, timeout):
        pedidos.append(params)
        return Resp(params)

    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=get))
    monkeypatch.setattr(mod_cdi, "CACHE_DIR", tmp_path)
    serie = mod_cdi.baixar_cdi("2014-01-01")
    assert len(pedidos) >= 3  # mais de 10 anos: várias consultas de até 5 anos
    assert serie.index.is_monotonic_increasing and not serie.index.duplicated().any()
    assert serie.iloc[0] == pytest.approx(0.0004)
    assert (tmp_path / "cdi.csv").exists()


def test_comparar_roda_todos_os_cenarios():
    from quant_b3 import comparar
    from quant_b3.cdi import retornos_por_pregao

    precos, volume = dados_sinteticos(n_dias=1200, n_ativos=25)
    bench = precos.mean(axis=1)
    cdi = retornos_por_pregao(None, precos.index, 0.10)
    tabela = comparar.rodar(precos, volume, CFG, cdi, bench, pd.Timestamp("2021-06-01"), "IDX")
    assert set(comparar.CENARIOS) | {"IDX", "CDI"} == set(tabela.index)
    assert tabela.loc["Base (config.toml)", ["cagr_calib", "cagr_valid"]].notna().all()
    assert tabela.loc["CDI", "cagr_valid"] == pytest.approx(0.10, abs=0.01)
    assert "Validação" in comparar.formatar(tabela, pd.Timestamp("2021-06-01"), precos.index[-1])


def test_fatores_pre_calculados_dao_o_mesmo_ranking():
    from quant_b3.fatores import calcular_fatores

    precos, volume = dados_sinteticos()
    data = precos.index[700]
    pre = calcular_fatores(precos, volume, CFG)
    pd.testing.assert_frame_equal(pontuar(precos, volume, data, CFG),
                                  pontuar(precos, volume, data, CFG, pre))


def test_robustez_grade_e_walk_forward():
    from quant_b3 import robustez
    from quant_b3.cdi import retornos_por_pregao

    precos, volume = dados_sinteticos(n_dias=2000, n_ativos=35, semente=5)
    precos.index = pd.bdate_range("2013-01-01", periods=len(precos))
    volume.index = precos.index
    bench = precos.mean(axis=1)
    cdi = retornos_por_pregao(None, precos.index, 0.10)
    cfg = {**CFG, "backtest": {**CFG["backtest"], "inicio": "2015-01-01"}}

    curvas = robustez.rodar(precos, volume, cfg, cdi, progresso=lambda _: None)
    assert len(curvas) == len(robustez.NUM_ATIVOS) * len(robustez.FAIXA_EXTRA) * len(robustez.MISTURAS)

    g = robustez.grade(curvas, cdi, bench, pd.Timestamp("2018-01-01"))
    assert g[["sharpe_calib", "cagr_valid", "anos_acima_bench"]].notna().all().all()
    assert "Só baixa vol" in robustez.tabela_cruzada(g, "cagr_valid", str)

    wf = robustez.walk_forward(curvas, cdi, bench, ["2017-01-01", "2018-01-01", "2030-01-01"])
    assert list(wf["corte"]) == [pd.Timestamp("2017-01-01"), pd.Timestamp("2018-01-01")]  # 2030 fora
    # a escolhida tem mesmo o maior Sharpe antes do corte
    from quant_b3 import metricas
    corte = pd.Timestamp("2017-01-01")
    melhor = max(curvas, key=lambda k: metricas.resumo(curvas[k].loc[:corte], cdi)["sharpe"])
    assert wf.iloc[0]["escolhida"] == f"{melhor[0]} | {melhor[1]}"
    assert "Escolhida" in robustez.formatar_walk_forward(wf, "IDX")
