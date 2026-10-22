"""Paper trading: carteira simulada persistida em CSV."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

COLUNAS = ["data", "ticker", "lado", "quantidade", "preco", "valor"]


class Carteira:
    def __init__(self, diretorio: Path):
        self.diretorio = Path(diretorio)
        self._estado = self.diretorio / "estado.json"
        self._operacoes = self.diretorio / "operacoes.csv"
        self.caixa = 0.0
        self.posicoes: dict[str, int] = {}
        if self.existe():
            dados = json.loads(self._estado.read_text())
            self.caixa = dados["caixa"]
            self.posicoes = dados["posicoes"]

    def existe(self) -> bool:
        return self._estado.exists()

    def iniciar(self, capital: float) -> None:
        self.diretorio.mkdir(parents=True, exist_ok=True)
        self.caixa, self.posicoes = float(capital), {}
        self._salvar()
        pd.DataFrame(columns=COLUNAS).to_csv(self._operacoes, index=False)

    def patrimonio(self, precos: pd.Series) -> float:
        return self.caixa + sum(q * float(precos[t]) for t, q in self.posicoes.items())

    def operacoes(self) -> pd.DataFrame:
        if not self._operacoes.exists():
            return pd.DataFrame(columns=COLUNAS)
        return pd.read_csv(self._operacoes, parse_dates=["data"])

    def vendas_no_mes(self, data: pd.Timestamp) -> float:
        ops = self.operacoes()
        if ops.empty:
            return 0.0
        mes = (ops["data"].dt.year == data.year) & (ops["data"].dt.month == data.month)
        return float(ops.loc[mes & (ops["lado"] == "VENDA"), "valor"].sum())

    def registrar(self, ordens: pd.DataFrame, data: pd.Timestamp) -> None:
        """Considera as ordens executadas ao preço informado (vendas primeiro)."""
        for o in ordens.itertuples():
            sinal = 1 if o.lado == "COMPRA" else -1
            nova = self.posicoes.get(o.ticker, 0) + sinal * o.quantidade
            if nova < 0:
                raise ValueError(f"Venda maior que a posição em {o.ticker}")
            self.caixa -= sinal * o.valor
            if nova:
                self.posicoes[o.ticker] = int(nova)
            else:
                self.posicoes.pop(o.ticker, None)
        if self.caixa < -0.01:
            raise ValueError("Caixa negativo após as ordens")
        novas = ordens.assign(data=pd.Timestamp(data).date())[COLUNAS]
        novas.to_csv(self._operacoes, mode="a", header=False, index=False)
        self._salvar()

    def _salvar(self) -> None:
        self._estado.write_text(json.dumps(
            {"caixa": round(self.caixa, 2), "posicoes": self.posicoes}, indent=2))
