# Carteira Quantitativa B3

Robô de carteira para ações da B3 com **rebalanceamento mensal**, combinando dois fatores:

- **Momentum 12-1**: retorno dos últimos 12 meses, ignorando o mês mais recente.
- **Baixa volatilidade**: volatilidade anualizada dos últimos 3 meses (quanto menor, melhor).

Pontuação = `peso_momentum × z(momentum) − peso_volatilidade × z(volatilidade)`.
As `num_ativos` melhores ações (após filtros de liquidez e preço) formam a carteira,
com pesos iguais ou pelo inverso da volatilidade.

Opções para reduzir giro e risco (em `config.toml`):

- **`frequencia_meses`**: rebalancear a cada N meses (ex.: 3 = trimestral).
- **`faixa_manutencao`**: uma ação em carteira só é vendida se sair das N melhores
  (ex.: 20). Evita trocar ações que oscilam em torno do 10º lugar.
- **`[tendencia]`**: se o benchmark (BOVA11) estiver abaixo da média de 200 dias,
  a carteira inteira vai para o caixa.

**Imposto de renda** (seção `[impostos]`): o backtest apura o IR mês a mês, como
no swing trade em ações. São 15% sobre o lucro, com isenção no mês em que as vendas
somam até R$ 20 mil e compensação de prejuízos, usando o preço médio. Para comparar
de forma justa, BOVA11 e CDI pagam 15% sobre o ganho ao vender no fim do período. A
apuração mensal fica em `resultados/impostos.csv`. O tamanho da carteira importa:
quanto menor o capital, mais meses ficam isentos. Não considera custos no preço
médio, IRRF, JCP nem a tributação de dividendos.

O caixa rende CDI, baixado da API do Banco Central (série SGS 12). Sem acesso a ela,
usa a taxa fixa `taxa_livre_risco_anual`. O CDI também é a referência do Sharpe.

> ⚠️ Projeto educacional. Resultado de backtest não garante resultado futuro.
> Não é recomendação de investimento.

## Instalação

```bash
cd carteira-quant-b3
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Requer Python 3.10+.

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

### 2. Comparar variações

```bash
python -m quant_b3 comparar                    # corte padrão em 01/2021
python -m quant_b3 comparar --corte 2020-01-01
```

Roda os cenários definidos em `quant_b3/comparar.py` (só momentum, só baixa
volatilidade, trimestral, faixa, filtro de tendência e combinações) e mostra as métricas
separadas em dois períodos:

- **Calibração** (antes do corte): use para escolher a configuração.
- **Validação** (depois do corte): confirma se a vantagem se mantém em dados que não
  influenciaram a escolha. Um cenário que só é bom na calibração provavelmente foi sorte.

A tabela é salva em `resultados/comparacao.csv`. Para adotar um cenário, copie os
parâmetros dele para o `config.toml`.

### 3. Testar robustez

```bash
python -m quant_b3 robustez
```

Roda 40 configurações (10 a 30 ativos, com e sem faixa de manutenção, 4 combinações de
pesos entre momentum e baixa volatilidade) e mostra:

1. **Tabelas cruzadas** de Sharpe na calibração, CAGR na validação, queda máxima e % dos
   anos acima do BOVA11. Uma configuração confiável tem vizinhos com números parecidos;
   uma célula isolada muito melhor que as vizinhas provavelmente é sorte.
2. **Walk-forward**: para cada corte (2019 a 2022), escolhe a configuração de melhor
   Sharpe com os dados *anteriores* e mede quanto ela rendeu *depois*. Se a escolhida
   não fica acima da mediana e do BOVA11 na maioria dos cortes, escolher pelo passado
   não funciona para essa estratégia.

Tabelas salvas em `resultados/robustez_grade.csv` e `resultados/robustez_walkforward.csv`.

### 4. Carteira do mês (paper trading)

```bash
python -m quant_b3 carteira                 # mostra ranking e ordens (não grava nada)
python -m quant_b3 carteira --registrar     # executa as ordens na carteira simulada
python -m quant_b3 carteira --capital 50000 # capital inicial da simulação (1ª execução)
```

A carteira simulada fica em `paper/` (`estado.json` e `operacoes.csv`). Rode no
primeiro pregão de cada mês (ou a cada `frequencia_meses`). A faixa de manutenção e o
filtro de tendência também valem aqui. O comando avisa quando as vendas de ações no mês
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
- **IR**: o backtest usa regras simplificadas (veja acima). No paper trading, o aviso
  de isenção é só um alerta, sem apuração do imposto.

## Próximos passos sugeridos

1. Rodar o backtest e testar a sensibilidade (número de ativos, pesos, janelas).
2. Acompanhar o paper trading por alguns meses.
3. Integrar com a corretora (ex.: MetaTrader 5) para enviar as ordens.
4. Adicionar cálculo de preço médio e apuração mensal de IR.
5. Enviar o ranking e as ordens do mês pelo Telegram.
