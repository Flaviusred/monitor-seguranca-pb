# Monitor de Segurança Pública · Paraíba

Painel web que coleta notícias sobre segurança pública na Paraíba (portais, blogs e órgãos oficiais),
classifica cada uma por tipo de crime, gravidade e município, agrupa notícias sobre o mesmo fato e
mostra tudo em tempo real num dashboard com mapa, filtros e gráficos.

## Como usar

1. Tenha o Python 3.10 ou mais recente instalado.
2. Dê dois cliques em **`iniciar.bat`**. Ele instala as dependências e abre o painel em
   <http://localhost:8000>.
3. Para parar, feche a janela preta do servidor.

A primeira coleta leva alguns segundos. Depois disso o servidor coleta sozinho a cada 5 minutos, e as
notícias novas aparecem no painel sem precisar recarregar. O botão **↻ Atualizar** força uma coleta na hora.

Para rodar pelo terminal, na pasta `monitor-seguranca-pb`:

```bash
python -m uvicorn main:app --app-dir app --port 8000
```

## Colocar num servidor

Para publicar num servidor de testes (Docker + HTTPS automático + senha de acesso), siga o
**[DEPLOY.md](DEPLOY.md)**. Em resumo: `empacotar.bat` → enviar o pacote → ajustar o `.env` →
`docker compose up -d --build`.

Para um servidor **gratuito** na Oracle Cloud, com endereço público e HTTPS sem comprar domínio, siga o
**[DEPLOY-ORACLE.md](DEPLOY-ORACLE.md)**.

## O que o painel mostra

- **Indicadores:** fatos noticiados, CVLI (crimes letais), alta repercussão, gravidade crítica e
  percentual com fonte oficial, comparados com o período anterior.
- **Mapa:** os 223 municípios (malha do IBGE) coloridos pela quantidade de ocorrências. Clique para filtrar.
- **Feed em tempo real:** com alerta na tela para fatos de gravidade alta ou crítica.
- **Gráficos:** evolução por gravidade, por tipo de crime e por mesorregião (clique para filtrar).
- **Rankings:** municípios com mais ocorrências e termos em alta nas manchetes.
- **Detalhe de cada fato:** links para todas as matérias que noticiaram o mesmo caso.
- **Filtros:** busca livre, período, mesorregião, município, gravidade, tipo de crime, tipo de fonte,
  alta repercussão, só com fonte oficial, só CVLI.
- **Exportação:** CSV do que estiver filtrado (abre no Excel).
- **Relatório (botão ▤):** escolha o período (qualquer intervalo de datas guardado no banco) e, se quiser,
  aplique os filtros do painel. O relatório traz o total de crimes e de CVLI, a lista de mortes violentas,
  a distribuição por tipo, mesorregião e município, a evolução diária (ou mensal, acima de 62 dias), a
  lista completa de crimes e, em anexo, as prisões, condenações e julgamentos de casos anteriores, fora dos
  números. Use "Imprimir / salvar PDF" para gerar o arquivo. O mesmo período também sai em CSV.
- **Histórico:** nada é apagado. Todas as notícias e fatos ficam no banco e podem ser consultados por período
  nos relatórios (a coleta só alcança notícias publicadas a partir da instalação).

## Como a classificação funciona

| Etapa | O que faz |
|---|---|
| Coleta | Lê os feeds RSS listados em `app/fontes.py` |
| Filtro | Descarta o que não é fato criminal na PB (concursos, política, estatísticas, outros estados) |
| Tipo e gravidade | Palavras-chave por tipo de crime; agravantes como criança, policial ou chacina sobem um nível |
| Município | Procura os nomes dos 223 municípios no título e no resumo; bairros conhecidos de João Pessoa e Campina Grande também identificam a cidade |
| Agrupamento | Notícias sobre o mesmo caso viram um único fato: manchetes com os mesmos detalhes (palavras específicas e números como "20 tiros"), em cidades iguais ou vizinhas, publicadas a até 72 h de qualquer outra notícia do grupo. Em municípios pequenos, crimes graves do mesmo tipo no mesmo dia também são unidos |
| Natureza | Cada notícia é classificada como **ocorrência** (o crime aconteceu agora), **prisão/investigação** de caso anterior, **condenação/julgamento** (júri, sentença, absolvição, audiência) ou **resumo/retrospectiva** (balanços, casos antigos relembrados). Só ocorrências entram nos números; as demais ficam fora do feed por padrão (opção "Mostrar desdobramentos") e vão para o anexo dos relatórios |
| Notícias repetidas | A mesma manchete do mesmo veículo, chegando por links diferentes, é guardada uma vez só |
| Repercussão | 0 a 100, pela gravidade, pelo número de veículos diferentes e pela presença de fonte oficial (70 ou mais = alta) |

**Regra de contagem:** indicadores, mapa, gráficos e rankings contam cada crime **uma vez**, não importa
quantos veículos o noticiaram, e não contam desdobramentos. O mapa e o gráfico por mesorregião deixam de
fora os crimes sem município identificado; por isso a soma deles pode ser menor que o total de crimes.

Quando as regras de classificação ou agrupamento mudam, o banco é reagrupado automaticamente na próxima
inicialização (com cópia de segurança em `dados/monitor.vN.bak`).

### Classificação com IA (opcional)

Por padrão a classificação é feita por regras, sem custo. Para usar o Claude, que entende melhor o
contexto e gera um resumo de cada fato, defina a chave da API da Anthropic antes de iniciar:

```bash
set ANTHROPIC_API_KEY=sua-chave-aqui
```

Cada coleta passa a enviar as notícias novas ao modelo `claude-opus-5-5`, e cada chamada tem custo.
Outro modelo pode ser escolhido com `CLAUDE_MODEL`. Se a chamada falhar, o sistema volta às regras
automaticamente. Para forçar só as regras mesmo com a chave definida, use `CLASSIFICADOR=regras`.

## Configurações (variáveis de ambiente)

| Variável | Padrão | Função |
|---|---|---|
| `COLETA_MINUTOS` | `5` | Intervalo entre coletas |
| `CLASSIFICADOR` | `auto` | `auto` (IA se houver chave), `regras` ou `claude` |
| `ANTHROPIC_API_KEY` | — | Ativa a classificação com IA |
| `CLAUDE_MODEL` | `claude-opus-5-5` | Modelo usado na classificação com IA |

## Fontes monitoradas

g1 Paraíba, ClickPB, MaisPB, Paraíba Online e quatro buscas no Google Notícias: crimes letais,
patrimônio e drogas, ação policial e sites `pb.gov.br`. Para incluir uma fonte com RSS, acrescente
uma linha em `FEEDS` no arquivo `app/fontes.py`.

**Limitações conhecidas**

- O site da Secretaria de Segurança e Defesa Social usa proteção anti-robô e não é lido diretamente.
  As notas oficiais só chegam quando aparecem no Google Notícias.
- Redes sociais ainda não são monitoradas, porque as plataformas restringem o acesso automatizado.
- Cerca de um terço das notícias não cita o município. Esses fatos aparecem no feed, mas não no mapa.
- A classificação é automática e pode errar. O detalhe de cada fato traz os links das matérias para conferência.

## Estrutura

```
monitor-seguranca-pb/
├── iniciar.bat              atalho para instalar e iniciar
├── requirements.txt
├── app/
│   ├── main.py              servidor, coleta periódica, API e tempo real (SSE)
│   ├── fontes.py            lista de feeds e tipo de cada fonte
│   ├── classificador.py     regras de tipo, gravidade, município e bairro
│   ├── ia.py                classificação opcional com o Claude
│   ├── db.py                banco SQLite e agrupamento de notícias
│   └── municipios_pb.json   223 municípios (IBGE) com mesorregião e coordenadas
├── web/
│   ├── index.html           painel
│   └── municipios_pb.geojson  contornos dos municípios (IBGE)
└── dados/monitor.db         banco criado na primeira execução
```

Para recomeçar do zero, apague `dados/monitor.db` com o servidor parado.

## API

| Rota | Descrição |
|---|---|
| `GET /api/eventos?dias=30` | Fatos dos últimos dias, com as notícias de cada um |
| `GET /api/eventos?inicio=2026-09-01&fim=2026-09-30` | Fatos de um intervalo de datas (usado pelos relatórios) |
| `GET /api/municipios` | Lista de municípios |
| `GET /api/status` | Última coleta, situação das fontes e modo de classificação |
| `POST /api/coletar` | Força uma coleta imediata |
| `GET /api/stream` | Fluxo em tempo real (Server-Sent Events) |
