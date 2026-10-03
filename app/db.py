"""Armazenamento em SQLite.

noticias: cada matéria coletada (uma linha por URL), com a classificação dela.
eventos:  o fato em si. Notícias de fontes diferentes sobre o mesmo crime são agrupadas no mesmo
          evento; os campos do evento são sempre recalculados a partir das notícias agrupadas.
"""
import json
import os
import shutil
import sqlite3
import threading
import time
from collections import Counter
from pathlib import Path

from classificador import CVLI, MUN_POR_NOME, OCORRENCIA, classificar, detalhes, limpa_titulo, mesmo_fato, natureza, norm, tokens

# MONITOR_DB permite apontar para outro arquivo (ex.: uma cópia para testes)
ARQ = Path(os.getenv("MONITOR_DB") or Path(__file__).resolve().parent.parent / "dados" / "monitor.db")
JANELA_AGRUPAMENTO = 72 * 3600 * 1000  # ms
VERSAO_ESQUEMA = 7

_lock = threading.Lock()
_con: sqlite3.Connection | None = None


def con() -> sqlite3.Connection:
    global _con
    if _con is None:
        ARQ.parent.mkdir(exist_ok=True)
        _con = sqlite3.connect(ARQ, check_same_thread=False)
        _con.row_factory = sqlite3.Row
        _con.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS eventos(
            id INTEGER PRIMARY KEY, titulo TEXT, resumo TEXT, tipo TEXT, cvli INTEGER, gravidade INTEGER,
            cidade TEXT, regiao TEXT, lat REAL, lng REAL, bairro TEXT,
            primeiro_em INTEGER, atualizado_em INTEGER, n_fontes INTEGER, oficial INTEGER, repercussao INTEGER,
            classificador TEXT, tokens TEXT, desdobramento INTEGER DEFAULT 0, ultimo_em INTEGER, natureza TEXT, detalhes TEXT);
        CREATE TABLE IF NOT EXISTS noticias(
            id INTEGER PRIMARY KEY, url TEXT UNIQUE, titulo TEXT, resumo TEXT, fonte TEXT, fonte_tipo TEXT,
            feed TEXT, publicado_em INTEGER, coletado_em INTEGER, evento_id INTEGER,
            tipo TEXT, gravidade INTEGER, cidade TEXT, bairro TEXT, resumo_ia TEXT, desdobramento INTEGER, classificador TEXT,
            natureza TEXT, assinatura TEXT);
        CREATE TABLE IF NOT EXISTS vistos(url TEXT PRIMARY KEY, em INTEGER);
        CREATE INDEX IF NOT EXISTS ix_ev_data ON eventos(primeiro_em);
        CREATE INDEX IF NOT EXISTS ix_not_ev ON noticias(evento_id);
        """)
        _migrar(_con)
    return _con


def _migrar(c: sqlite3.Connection) -> None:
    """Atualiza bancos criados por versões anteriores e reagrupa os eventos com as regras atuais."""
    versao = c.execute("PRAGMA user_version").fetchone()[0]
    if versao >= VERSAO_ESQUEMA:
        return
    if c.execute("SELECT COUNT(*) FROM noticias").fetchone()[0]:
        shutil.copy2(ARQ, ARQ.with_suffix(f".v{versao}.bak"))  # cópia de segurança antes de converter
    for tabela, coluna, tipo in [("eventos", "desdobramento", "INTEGER DEFAULT 0"), ("eventos", "ultimo_em", "INTEGER"), ("noticias", "tipo", "TEXT"),
                                 ("noticias", "gravidade", "INTEGER"), ("noticias", "cidade", "TEXT"),
                                 ("noticias", "bairro", "TEXT"), ("noticias", "resumo_ia", "TEXT"),
                                 ("noticias", "desdobramento", "INTEGER"), ("noticias", "classificador", "TEXT"),
                                 ("eventos", "natureza", "TEXT"), ("eventos", "detalhes", "TEXT"), ("noticias", "natureza", "TEXT"), ("noticias", "assinatura", "TEXT")]:
        colunas = {r[1] for r in c.execute(f"PRAGMA table_info({tabela})")}
        if coluna not in colunas:
            c.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo}")
    # títulos vindos do Google Notícias guardados com o sufixo do veículo ("... - G1")
    for n in c.execute("SELECT id, titulo FROM noticias WHERE feed LIKE 'Google%'").fetchall():
        limpo = limpa_titulo(n["titulo"])
        if limpo != n["titulo"]:
            c.execute("UPDATE noticias SET titulo=? WHERE id=?", (limpo, n["id"]))
    # a mesma matéria do mesmo veículo chegando por links diferentes: fica só a primeira
    for n in c.execute("SELECT id, titulo FROM noticias").fetchall():
        c.execute("UPDATE noticias SET assinatura=? WHERE id=?", (assinatura(n["titulo"]), n["id"]))
    c.execute("DELETE FROM noticias WHERE id NOT IN (SELECT MIN(id) FROM noticias GROUP BY fonte, assinatura)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_not_assin ON noticias(fonte, assinatura)")
    _reagrupar(c)
    _eventos_removidos.clear()
    c.execute(f"PRAGMA user_version={VERSAO_ESQUEMA}")
    c.commit()


def ja_visto(urls: list[str]) -> set[str]:
    """URLs já processadas (relevantes ou descartadas), para não reclassificar."""
    if not urls:
        return set()
    with _lock:
        q = ",".join("?" * len(urls))
        return {r[0] for r in con().execute(f"SELECT url FROM vistos WHERE url IN ({q})", urls)}


def marcar_vistos(urls: list[str]) -> None:
    agora = int(time.time() * 1000)
    with _lock, con():
        con().executemany("INSERT OR IGNORE INTO vistos VALUES(?,?)", [(u, agora) for u in urls])


def assinatura(titulo: str) -> str:
    """Manchete normalizada: sem acentos, caixa, pontuação e sufixo do veículo."""
    return " ".join(norm(limpa_titulo(titulo)).replace("'", " ").split())


def repercussao(gravidade: int, n_fontes: int, oficial: bool) -> int:
    return min(100, gravidade * 15 + (n_fontes - 1) * 15 + (10 if oficial else 0))


# ---------------------------------------------------------------- agrupamento
def _microrregiao(cidade: str | None) -> str | None:
    m = MUN_POR_NOME.get(norm(cidade or ""))
    return m and m["microrregiao"]


def _cidades_compativeis(a: str | None, b: str | None) -> bool:
    """Mesma cidade, cidade desconhecida em um dos lados, ou cidades vizinhas (mesma microrregião:
    'Grande João Pessoa' cobre Bayeux, Santa Rita etc.)."""
    return not a or not b or a == b or _microrregiao(a) == _microrregiao(b)


# Municípios com mais de ~60 mil habitantes: vários crimes graves no mesmo dia são comuns,
# então só se agrupa por semelhança de manchete
_GRANDES = {"João Pessoa", "Campina Grande", "Santa Rita", "Patos", "Bayeux", "Sousa", "Cabedelo", "Cajazeiras",
            "Guarabira", "Sapé"}
_JANELA_CIDADE_PEQUENA = 36 * 3600 * 1000
_JANELA_CVLI = 12 * 3600 * 1000
_JANELA_DETALHE = 24 * 3600 * 1000



def _distancia(a: dict, b: dict) -> int:
    """Intervalo (ms) entre os períodos de notícias de dois registros; 0 se os períodos se sobrepõem."""
    return max(0, b["ini"] - a["fim"], a["ini"] - b["fim"])


def _pontuacao(a: dict, b: dict) -> float:
    """Chance (0-1) de dois registros {tipo, cidade, tokens, ini, fim, gravidade} serem o mesmo fato.
    `tokens` é uma lista com as palavras de cada manchete; vale o par de manchetes mais parecido.
    A janela de tempo conta a partir de qualquer notícia do grupo (casos noticiados por vários dias)."""
    if _distancia(a, b) > JANELA_AGRUPAMENTO or not _cidades_compativeis(a["cidade"], b["cidade"]):
        return 0.0
    mesmo_tipo = a["tipo"] == b["tipo"]
    s = max((mesmo_fato(x, y, mesmo_tipo) for x in a["tokens"] for y in b["tokens"]), default=0.0)
    if s or not (a["ocorrencia"] and b["ocorrencia"]) or not a["cidade"] or a["cidade"] != b["cidade"]:
        return s
    # As regras abaixo só unem dois CRIMES (nunca um crime e a condenação/prisão de outro caso).
    # O tempo aqui é medido entre as PRIMEIRAS notícias de cada caso (o momento do crime), para a cobertura
    # que se estende por dias não emendar casos diferentes
    entre_crimes = abs(a["ini"] - b["ini"])
    # Detalhes do texto (bairro, idade da vítima): iguais indicam o mesmo caso; diferentes indicam casos distintos
    ba, bb, ia, ib = set(a["det"]["b"]), set(b["det"]["b"]), set(a["det"]["i"]), set(b["det"]["i"])
    bairro_igual, idade_igual = bool(ba & bb), bool(ia & ib)
    bairro_diferente = bool(ba and bb and not ba & bb)
    idade_diferente = bool(ia and ib and not ia & ib)
    # Município pequeno: dois crimes graves do mesmo tipo no mesmo dia e lugar quase sempre são o mesmo caso,
    # mesmo quando as manchetes não têm palavras em comum
    if (a["tipo"] == b["tipo"] and a["cidade"] not in _GRANDES and min(a["gravidade"], b["gravidade"]) >= 3
            and entre_crimes <= _JANELA_CIDADE_PEQUENA and not bairro_diferente and not idade_diferente):
        return 0.5
    if a["tipo"] in CVLI and b["tipo"] in CVLI and not idade_diferente:
        # Morte violenta na mesma cidade e no mesmo bairro, ou com vítima da mesma idade, em até 24 h:
        # é o mesmo caso noticiado com outras palavras (ex.: "morto enquanto caminhava em rua" e
        # "morto no Treze de Maio", ambos no bairro Treze de Maio)
        if entre_crimes <= _JANELA_DETALHE and (bairro_igual or (idade_igual and not bairro_diferente)):
            return 0.6
        # Poucas horas de diferença e alguma palavra em comum na manchete ("casa", circunstância)
        if (entre_crimes <= _JANELA_CVLI and not bairro_diferente
                and any({w for w in x & y if not w.startswith("=")} for x in a["tokens"] for y in b["tokens"])):
            return 0.5
    return 0.0


def _ev_info(ev: sqlite3.Row) -> dict:
    return {"id": ev["id"], "tipo": ev["tipo"], "cidade": ev["cidade"], "tokens": [set(t) for t in json.loads(ev["tokens"])],
            "ini": ev["primeiro_em"], "fim": ev["ultimo_em"] or ev["primeiro_em"], "gravidade": ev["gravidade"] or 1,
            "ocorrencia": not ev["desdobramento"], "det": json.loads(ev["detalhes"] or '{"b": [], "i": []}')}


def _candidatos(c: sqlite3.Connection, ini: int, fim: int, exceto: int | None = None) -> list[dict]:
    return [_ev_info(ev) for ev in c.execute(
        "SELECT * FROM eventos WHERE IFNULL(ultimo_em, primeiro_em) >= ? AND primeiro_em <= ? AND id IS NOT ?",
        (ini - JANELA_AGRUPAMENTO, fim + JANELA_AGRUPAMENTO, exceto))]


def _agrupar(c: sqlite3.Connection, n: sqlite3.Row) -> int:
    """Coloca a notícia num evento existente (se for o mesmo fato) ou cria um novo. Devolve o id do evento."""
    info = {"tipo": n["tipo"], "cidade": n["cidade"], "tokens": [tokens(n["titulo"])], "ini": n["publicado_em"], "fim": n["publicado_em"],
            "gravidade": n["gravidade"], "ocorrencia": n["natureza"] == OCORRENCIA, "det": detalhes(n["titulo"], n["resumo"])}
    melhor, alvo = 0.0, None
    for ev in _candidatos(c, n["publicado_em"], n["publicado_em"]):
        s = _pontuacao(info, ev)
        if s > melhor:
            melhor, alvo = s, ev["id"]
    if alvo is None:
        alvo = c.execute("INSERT INTO eventos(tokens, primeiro_em) VALUES('[]', ?)", (n["publicado_em"],)).lastrowid
    c.execute("UPDATE noticias SET evento_id=? WHERE id=?", (alvo, n["id"]))
    _recalcular(c, alvo)
    return _fundir(c, alvo)


def _fundir(c: sqlite3.Connection, ev_id: int) -> int:
    """Depois que um evento ganha notícias, ele pode se revelar o mesmo fato de outro evento já existente
    (a ordem de chegada das notícias não deve importar). Une os dois, mantendo o id mais antigo."""
    while True:
        ev = _ev_info(c.execute("SELECT * FROM eventos WHERE id=?", (ev_id,)).fetchone())
        outro = next((o for o in _candidatos(c, ev["ini"], ev["fim"], exceto=ev_id) if _pontuacao(ev, o)), None)
        if outro is None:
            return ev_id
        manter, remover = sorted((ev_id, outro["id"]))
        c.execute("UPDATE noticias SET evento_id=? WHERE evento_id=?", (manter, remover))
        c.execute("DELETE FROM eventos WHERE id=?", (remover,))
        _eventos_removidos.add(remover)
        _recalcular(c, manter)
        ev_id = manter


# ids de eventos absorvidos por outros desde a última leitura (o painel precisa removê-los)
_eventos_removidos: set[int] = set()


def tirar_removidos() -> list[int]:
    with _lock:
        ids = sorted(_eventos_removidos)
        _eventos_removidos.clear()
        return ids


def _recalcular(c: sqlite3.Connection, ev_id: int) -> None:
    """Deriva os campos do evento a partir de todas as notícias agrupadas nele."""
    ns = c.execute("SELECT * FROM noticias WHERE evento_id=? ORDER BY publicado_em", (ev_id,)).fetchall()
    # notícia principal: a que relata o crime (não a prisão posterior), a mais grave, a mais antiga
    principal = min(ns, key=lambda n: (n["natureza"] != OCORRENCIA, -n["gravidade"], n["publicado_em"]))
    ocorrencias = [n for n in ns if n["natureza"] == OCORRENCIA] or ns
    gravidade = max(n["gravidade"] for n in ocorrencias)
    cidades = Counter(n["cidade"] for n in ns if n["cidade"])
    cidade = principal["cidade"] or (cidades.most_common(1)[0][0] if cidades else None)
    mun = MUN_POR_NOME.get(norm(cidade or ""))
    fontes = {n["fonte"] for n in ns}
    oficial = any(n["fonte_tipo"] == "Portal oficial" for n in ns)
    toks = [sorted(t) for t in {frozenset(tokens(n["titulo"])) for n in ns} if t]
    dets = [detalhes(n["titulo"], n["resumo"]) for n in ns]
    det = {"b": sorted({b for d in dets for b in d["b"]}), "i": sorted({i for d in dets for i in d["i"]})}
    c.execute("""UPDATE eventos SET titulo=?, resumo=?, tipo=?, cvli=?, gravidade=?, cidade=?, regiao=?, lat=?, lng=?, bairro=?,
                 primeiro_em=?, atualizado_em=?, n_fontes=?, oficial=?, repercussao=?, classificador=?, tokens=?, desdobramento=?,
                 ultimo_em=?, natureza=?, detalhes=? WHERE id=?""",
              (principal["titulo"], principal["resumo_ia"] or next((n["resumo_ia"] for n in ns if n["resumo_ia"]), None),
               principal["tipo"], int(principal["tipo"] in CVLI), gravidade,
               mun and mun["nome"], mun and mun["mesorregiao"], mun and mun["lat"], mun and mun["lng"],
               principal["bairro"] or next((n["bairro"] for n in ns if n["bairro"]), None),
               ns[0]["publicado_em"], int(time.time() * 1000), len(fontes), int(oficial),
               repercussao(gravidade, len(fontes), oficial), principal["classificador"], json.dumps(toks),
               int(principal["natureza"] != OCORRENCIA), ns[-1]["publicado_em"], principal["natureza"], json.dumps(det), ev_id))



def _reagrupar(c: sqlite3.Connection) -> None:
    """Refaz todos os eventos a partir das notícias guardadas. Notícias classificadas por regras (ou ainda
    sem classificação) são reclassificadas com as regras atuais; as que deixaram de ser relevantes são
    removidas. Classificações feitas pela IA são mantidas."""
    for n in c.execute("SELECT id, titulo, resumo FROM noticias WHERE tipo IS NULL OR classificador='regras'").fetchall():
        cls = classificar(n["titulo"], n["resumo"] or "")
        if cls is None:
            c.execute("DELETE FROM noticias WHERE id=?", (n["id"],))
        else:
            c.execute("UPDATE noticias SET tipo=?, gravidade=?, cidade=?, bairro=?, desdobramento=?, natureza=?, "
                      "classificador='regras' WHERE id=?",
                      (cls["tipo"], cls["gravidade"], cls["cidade"], cls["bairro"], int(cls["desdobramento"]),
                       cls["natureza"], n["id"]))
    # classificadas pela IA antes de existir o campo natureza: usa as regras só para esse campo
    for n in c.execute("SELECT id, titulo, tipo FROM noticias WHERE natureza IS NULL").fetchall():
        nat = natureza(n["titulo"], n["tipo"])
        c.execute("UPDATE noticias SET natureza=?, desdobramento=? WHERE id=?", (nat, int(nat != OCORRENCIA), n["id"]))
    c.execute("DELETE FROM eventos")
    c.execute("UPDATE noticias SET evento_id=NULL")
    for n in c.execute("SELECT * FROM noticias ORDER BY publicado_em").fetchall():
        _agrupar(c, n)


def reagrupar() -> int:
    """Reagrupa a base inteira (usar após mudar as regras de agrupamento). Devolve o total de eventos."""
    with _lock, con():
        shutil.copy2(ARQ, ARQ.with_suffix(".antes-reagrupar.bak"))
        _reagrupar(con())
        return con().execute("SELECT COUNT(*) FROM eventos").fetchone()[0]


def salvar(noticia: dict, cls: dict, classificador: str) -> int | None:
    """Grava a notícia classificada e a agrupa. Devolve o id do evento (None se a notícia já existia:
    mesma URL, ou a mesma manchete do mesmo veículo chegando por outro link)."""
    agora = int(time.time() * 1000)
    assin = assinatura(noticia["titulo"])
    with _lock, con():
        c = con()
        if c.execute("SELECT 1 FROM noticias WHERE fonte=? AND assinatura=?", (noticia["fonte"], assin)).fetchone():
            return None
        nat = cls.get("natureza") or natureza(noticia["titulo"], cls["tipo"])
        cur = c.execute(
            """INSERT OR IGNORE INTO noticias(url,titulo,resumo,fonte,fonte_tipo,feed,publicado_em,coletado_em,
               tipo,gravidade,cidade,bairro,resumo_ia,desdobramento,classificador,natureza,assinatura)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (noticia["url"], noticia["titulo"], noticia["resumo"], noticia["fonte"], noticia["fonte_tipo"], noticia["feed"],
             noticia["publicado_em"], agora, cls["tipo"], cls["gravidade"], cls["cidade"], cls.get("bairro"),
             cls.get("resumo"), int(nat != OCORRENCIA), classificador, nat, assin))
        if not cur.rowcount:
            return None
        return _agrupar(c, c.execute("SELECT * FROM noticias WHERE id=?", (cur.lastrowid,)).fetchone())


# ---------------------------------------------------------------- leitura
def _evento_dict(ev: sqlite3.Row, noticias: list[sqlite3.Row]) -> dict:
    d = {k: ev[k] for k in ev.keys() if k != "tokens"}
    d["cvli"] = bool(d["cvli"])
    d["oficial"] = bool(d["oficial"])
    d["desdobramento"] = bool(d["desdobramento"])
    d["noticias"] = [{k: n[k] for k in ("url", "titulo", "fonte", "fonte_tipo", "publicado_em", "natureza")}
                     for n in noticias]
    return d


def eventos(desde_ms: int = 0, ids: list[int] | None = None, ate_ms: int | None = None) -> list[dict]:
    with _lock:
        c = con()
        if ids is not None:
            if not ids:
                return []
            evs = c.execute(f"SELECT * FROM eventos WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
        else:
            evs = c.execute("SELECT * FROM eventos WHERE primeiro_em>=? AND primeiro_em<? ORDER BY primeiro_em DESC",
                            (desde_ms, ate_ms if ate_ms is not None else 2**62)).fetchall()
        por_ev: dict[int, list] = {}
        if evs:
            q = ",".join("?" * len(evs))
            for n in c.execute(f"SELECT * FROM noticias WHERE evento_id IN ({q}) ORDER BY publicado_em",
                               [e["id"] for e in evs]):
                por_ev.setdefault(n["evento_id"], []).append(n)
        return [_evento_dict(e, por_ev.get(e["id"], [])) for e in evs]
