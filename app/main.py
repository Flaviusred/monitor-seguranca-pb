"""Servidor do Monitor de Segurança Pública da Paraíba.

Executar (na pasta monitor-seguranca-pb):  python -m uvicorn main:app --app-dir app --port 8000
"""
import asyncio
import base64
import calendar
import html
import json
import logging
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

import classificador
import db
import ia
from fontes import FEEDS, tipo_por_dominio

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("monitor")

INTERVALO_MIN = float(os.getenv("COLETA_MINUTOS", "5"))
MAX_IDADE_DIAS = 30
WEB = Path(__file__).resolve().parent.parent / "web"

# Só notícias com algum termo policial vão para a IA (economiza chamadas com esporte, política etc.)
_PRE_FILTRO = re.compile(r"polic|crim|mort|morre|tiro|pres[oa]|prisao|assalt|roub|furt|drog|arma|violen|estupr|homicid|"
                         r"feminic|latrocin|operac|delegad|vitima|suspeit|balead|faca|agred|apreen|trafic|bandid|facc|"
                         r"assassin|cadaver|corpo|explos|caixa eletronico")
estado = {"ultima_coleta": None, "proxima_coleta": None, "coletando": False, "fontes": {}, "ultimos_novos": 0}
_assinantes: set[asyncio.Queue] = set()


# ---------------------------------------------------------------- coleta
def _limpa(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _dominio(url: str) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.")


_NOME_POR_DOMINIO = {_dominio(f["url"]): f["nome"] for f in FEEDS if not f.get("agregador")}


async def _baixar(cli: httpx.AsyncClient, feed: dict) -> list[dict]:
    r = await cli.get(feed["url"])
    r.raise_for_status()
    p = feedparser.parse(r.content)
    limite = (time.time() - MAX_IDADE_DIAS * 86400) * 1000
    itens = []
    for e in p.entries:
        url = e.get("link")
        if not url:
            continue
        data = e.get("published_parsed") or e.get("updated_parsed")
        pub = calendar.timegm(data) * 1000 if data else int(time.time() * 1000)
        if pub < limite:
            continue
        titulo = _limpa(e.get("title", ""))
        fonte, fonte_url = feed["nome"], url
        if feed.get("agregador") and e.get("source"):
            fonte_url = e.source.get("href") or url
            # mesmo veículo que já temos por feed direto: usa o mesmo nome, para não contar duas fontes
            fonte = _NOME_POR_DOMINIO.get(_dominio(fonte_url)) or e.source.get("title") or fonte
            titulo = classificador.limpa_titulo(re.sub(r"\s+-\s+" + re.escape(e.source.get("title") or fonte) + r"$", "", titulo))
        resumo = _limpa(e.get("summary", ""))
        if feed.get("agregador"):
            resumo = ""  # no Google Notícias o resumo só repete o título
        itens.append({"url": url, "titulo": titulo, "resumo": resumo, "fonte": fonte, "feed": feed["nome"],
                      "fonte_tipo": tipo_por_dominio(fonte_url, feed["tipo"]), "publicado_em": min(pub, int(time.time() * 1000))})
    return itens


async def coletar() -> list[int]:
    """Uma rodada de coleta. Devolve os ids de eventos criados ou atualizados."""
    if estado["coletando"]:
        return []
    estado["coletando"] = True
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0 (MonitorSegurancaPB)"}) as cli:
            resultados = await asyncio.gather(*(_baixar(cli, f) for f in FEEDS), return_exceptions=True)
        novos, vistos_rodada = [], set()
        for feed, res in zip(FEEDS, resultados):
            if isinstance(res, Exception):
                estado["fontes"][feed["nome"]] = {"ok": False, "erro": str(res)[:200], "itens": 0}
                log.warning("Falha em %s: %s", feed["nome"], res)
                continue
            estado["fontes"][feed["nome"]] = {"ok": True, "erro": None, "itens": len(res)}
            for it in res:
                if it["url"] not in vistos_rodada:
                    vistos_rodada.add(it["url"])
                    novos.append(it)
        ja = db.ja_visto([n["url"] for n in novos])
        novos = [n for n in novos if n["url"] not in ja]

        cls_lista, metodo = None, "regras"
        if novos and ia.ativo():
            try:
                candidatos = [i for i, n in enumerate(novos) if _PRE_FILTRO.search(classificador.norm(n["titulo"] + " " + n["resumo"]))]
                res = await ia.classificar_lote([novos[i] for i in candidatos])
                cls_lista = [None] * len(novos)
                for i, c in zip(candidatos, res):
                    cls_lista[i] = c
                metodo = "claude"
            except Exception as ex:  # qualquer falha da IA cai para as regras
                log.warning("Classificação por IA falhou, usando regras: %s", ex)
        if cls_lista is None:
            cls_lista = [classificador.classificar(n["titulo"], n["resumo"]) for n in novos]

        alterados = set()
        for n, cls in sorted(zip(novos, cls_lista), key=lambda x: x[0]["publicado_em"]):
            if cls and (ev := db.salvar(n, cls, metodo)) is not None:
                alterados.add(ev)
        db.marcar_vistos([n["url"] for n in novos])
        estado["ultima_coleta"] = int(time.time() * 1000)
        estado["ultimos_novos"] = len(alterados)
        log.info("Coleta: %d itens novos, %d relevantes, %d eventos alterados (%s)",
                 len(novos), sum(1 for c in cls_lista if c), len(alterados), metodo)
        removidos = db.tirar_removidos()  # eventos absorvidos por outros (mesmo fato)
        alterados -= set(removidos)
        if alterados or removidos:
            await _publicar(db.eventos(ids=list(alterados)), removidos)
        return list(alterados)
    finally:
        estado["coletando"] = False


async def _laco():
    while True:
        try:
            await coletar()
        except Exception:
            log.exception("Erro na coleta")
        estado["proxima_coleta"] = int((time.time() + INTERVALO_MIN * 60) * 1000)
        await asyncio.sleep(INTERVALO_MIN * 60)


async def _publicar(eventos: list[dict], removidos: list[int]):
    msg = json.dumps({"eventos": eventos, "removidos": removidos}, ensure_ascii=False)
    for q in list(_assinantes):
        q.put_nowait(msg)


@asynccontextmanager
async def ciclo(_app):
    tarefa = asyncio.create_task(_laco())
    yield
    tarefa.cancel()


# ---------------------------------------------------------------- API
app = FastAPI(title="Monitor de Segurança Pública PB", lifespan=ciclo)

# Senha de acesso opcional (recomendada em servidor exposto na internet): defina ACESSO_USUARIO e ACESSO_SENHA
_USUARIO, _SENHA = os.getenv("ACESSO_USUARIO", ""), os.getenv("ACESSO_SENHA", "")
_LIVRES = {"/api/saude"}  # verificação de funcionamento não pede senha


@app.middleware("http")
async def exigir_senha(request: Request, chamar):
    if _USUARIO and _SENHA and request.url.path not in _LIVRES:
        try:
            tipo, valor = request.headers.get("authorization", "").split(" ", 1)
            usuario, _, senha = base64.b64decode(valor).decode("utf-8").partition(":")
            ok = (tipo.lower() == "basic" and secrets.compare_digest(usuario, _USUARIO)
                  and secrets.compare_digest(senha, _SENHA))
        except Exception:
            ok = False
        if not ok:
            return Response("Acesso restrito", status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="Monitor Seguranca PB", charset="UTF-8"'})
    return await chamar(request)


@app.get("/api/eventos")
def api_eventos(dias: int = Query(30, ge=1, le=3650),
                inicio: date | None = Query(None, description="data inicial (AAAA-MM-DD), para relatórios"),
                fim: date | None = Query(None, description="data final, inclusive (AAAA-MM-DD)")):
    """Fatos do período. Com `inicio`/`fim` (horário de Brasília) devolve um intervalo fechado de datas;
    sem eles, os últimos `dias`."""
    if inicio:
        ate = _ms_meia_noite(fim + timedelta(days=1)) if fim else None
        return db.eventos(desde_ms=_ms_meia_noite(inicio), ate_ms=ate)
    return db.eventos(desde_ms=int((time.time() - dias * 86400) * 1000))


_BRASILIA = timezone(timedelta(hours=-3))


def _ms_meia_noite(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=_BRASILIA).timestamp() * 1000)


@app.get("/api/municipios")
def api_municipios():
    return classificador.MUNICIPIOS


@app.get("/api/status")
def api_status():
    return {**estado, "classificador": "claude" if ia.ativo() else "regras", "intervalo_min": INTERVALO_MIN,
            "modelo": ia.MODELO if ia.ativo() else None}


INTERVALO_MIN_MANUAL = 60  # segundos entre coletas pedidas pelo botão "Atualizar"


@app.post("/api/coletar")
async def api_coletar():
    # sem esse limite, qualquer visitante poderia disparar coletas em sequência e sobrecarregar os portais
    if estado["ultima_coleta"] and time.time() * 1000 - estado["ultima_coleta"] < INTERVALO_MIN_MANUAL * 1000:
        return JSONResponse({"eventos_alterados": 0, "aviso": "Coleta recente; aguarde um minuto."}, status_code=429)
    ids = await coletar()
    return {"eventos_alterados": len(ids)}


@app.get("/api/saude")
def api_saude():
    """Verificação de funcionamento (usada pelo Docker e pelo monitoramento do servidor)."""
    db.eventos(desde_ms=int(time.time() * 1000))  # confirma que o banco responde
    return {"ok": True, "ultima_coleta": estado["ultima_coleta"]}


@app.get("/api/stream")
async def api_stream():
    """Server-Sent Events: envia eventos novos/atualizados assim que a coleta os encontra."""
    q: asyncio.Queue = asyncio.Queue()
    _assinantes.add(q)

    async def gerar():
        try:
            yield "retry: 5000\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=25)
                    yield f"event: eventos\ndata: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            _assinantes.discard(q)

    # X-Accel-Buffering: impede que um proxy (nginx) segure as mensagens em buffer e quebre o tempo real
    return StreamingResponse(gerar(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/")
def index():
    # no-cache: o navegador revalida a cada acesso e sempre recebe a versão atual do painel
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=WEB), name="web")
