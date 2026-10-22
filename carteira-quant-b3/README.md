# Carteira Quantitativa B3

Robô de carteira para ações da B3 com **rebalanceamento mensal**, combinando dois fatores:

- **Momentum 12-1**: retorno dos últimos 12 meses, ignorando o mês mais recente.
- **Baixa volatilidade**: volatilidade anualizada dos últimos 3 meses (quanto menor, melhor).

Pontuação = `peso_momentum × z(momentum) − peso_volatilidade × z(volatilidade)`.
As `num_ativos` melhores ações (após filtros de liquidez e preço) formam a carteira,
com pesos iguais ou pelo inverso da volatilidade.

> ⚠️ Projeto educacional. Resultado de backtest não garante resultado futuro.
> Não é recomendação de investimento.

## Instalação

```bash
cd carteira-quant-b3
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Requer Python 3.11+.

## Uso

### 1. Backtest

```bash
python -m quant_b3 backtest
```

Baixa os preços ajustados do Yahoo Finance (com cache de 12 h em `cache/`), simula a
estratégia desde `[backtest].inicio` e compara com o `BOVA11`. Saídas em `resultados/`:
`patrimonio.csv`, `rebalanceamentos.csv` e `patrimonio.png` (se o matplotlib estiver instalado).

O backtest:
- usa só dados disponíveis até a data do sinal (último pregão do mês);
- executa as ordens `atraso_execucao` pregões depois do sinal;
- desconta `custo_bps` sobre todo o valor negociado;
- não compra ações sem preço no dia (deslistadas ou suspensas).

### 2. Carteira do mês (paper trading)

```bash
python -m quant_b3 carteira                 # mostra ranking e ordens (não grava nada)
python -m quant_b3 carteira --registrar     # executa as ordens na carteira simulada
python -m quant_b3 carteira --capital 50000 # capital inicial da simulação (1ª execução)
```

A carteira simulada fica em `paper/` (`estado.json` e `operacoes.csv`). Rode no
primeiro pregão de cada mês. O comando avisa quando as vendas de ações no mês
passam da isenção de R$ 20 mil do IR (swing trade).

### Dados próprios (CSV)

Para usar outra fonte (MetaTrader 5, COTAHIST da B3, brapi.dev etc.), gere CSVs
"largos", com a data na 1ª coluna e uma coluna por ticker:

```bash
python -m quant_b3 --precos-csv precos.csv --volume-csv volume.csv backtest
```

`precos.csv` deve ter preços **ajustados por proventos**, e `volume.csv` o volume financeiro diário em R$.

## Configuração

Tudo fica em [`config.toml`](config.toml): universo de ações, filtros, janelas e pesos
dos fatores, número de ativos, lote (1 = fracionário, 100 = lote padrão) e custos.

## Testes

```bash
python -m pytest
```

Os testes usam dados sintéticos e não precisam de internet.

## Limitações conhecidas

- **Viés de sobrevivência**: o universo do `config.toml` contém empresas listadas hoje.
  Empresas que faliram ou saíram da bolsa não entram, o que tende a inflar o backtest.
- **Dados do Yahoo**: gratuitos, mas podem ter falhas e ajustes imperfeitos. Para uso
  sério, valide com outra fonte.
- **Sem fatores fundamentalistas** (P/L, ROE etc.): fazer backtest com eles exige dados
  *point-in-time*, que não são gratuitos.
- **IR**: o aviso de isenção é só um alerta. Não calcula preço médio nem imposto devido.

## Próximos passos sugeridos

1. Rodar o backtest e testar a sensibilidade (número de ativos, pesos, janelas).
2. Acompanhar o paper trading por alguns meses.
3. Integrar com a corretora (ex.: MetaTrader 5) para enviar as ordens.
4. Adicionar cálculo de preço médio e apuração mensal de IR.
5. Enviar o ranking e as ordens do mês pelo Telegram.
