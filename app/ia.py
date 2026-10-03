"""Classificação opcional com o Claude (ativa com CLASSIFICADOR=claude ou com ANTHROPIC_API_KEY definida).

Envia as notícias novas em lotes e recebe JSON validado por esquema. Se a chamada falhar,
o coletor usa o classificador por regras para o lote.
"""
import json
import logging
import os

import anthropic

from classificador import CVLI, JUSTICA, MUN_POR_NOME, OCORRENCIA, PRISAO, RESUMO, TIPOS_NOMES, norm

NATUREZAS = [OCORRENCIA, PRISAO, JUSTICA, RESUMO]

log = logging.getLogger("monitor.ia")
MODELO = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
LOTE = 20

_SISTEMA = f"""Você classifica notícias para um painel de monitoramento da segurança pública do estado da Paraíba (Brasil).
Para cada notícia, decida:
- relevante: true só se a notícia relata um fato criminal concreto ou uma ação policial ocorrida na Paraíba. \
Concursos, editais, eventos institucionais, opinião, política e fatos de outros estados são false.
- tipo: um destes valores exatos: {", ".join(TIPOS_NOMES)}. Use o crime principal do fato.
- cidade: o município da Paraíba onde o fato ocorreu, com a grafia oficial, ou null se não for possível saber.
- bairro: bairro ou distrito citado, ou null.
- gravidade: 1 (baixa: furto, apreensão, operação de rotina), 2 (média: roubo, tráfico, violência doméstica), \
3 (alta: homicídio, tentativa de homicídio, crime sexual) ou 4 (crítica: feminicídio, latrocínio, chacina, ataque a banco, \
vítimas crianças ou policiais, grande comoção).
- natureza: o que a notícia relata. "ocorrência" = o crime em si, acontecido agora (inclui apreensões e prisões \
em flagrante); "prisão/investigação" = prisão, captura ou investigação ligada a um crime ocorrido antes; \
"condenação/julgamento" = júri, sentença, condenação, absolvição, audiência; "resumo/retrospectiva" = balanço de \
vários casos ou caso antigo relembrado. Só ocorrências entram nas estatísticas.
- resumo: uma frase objetiva em português, sem nomes de vítimas ou suspeitos."""

_ESQUEMA = {
    "type": "object",
    "properties": {"itens": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "indice": {"type": "integer"},
            "relevante": {"type": "boolean"},
            "tipo": {"type": "string", "enum": TIPOS_NOMES},
            "cidade": {"type": ["string", "null"]},
            "bairro": {"type": ["string", "null"]},
            "gravidade": {"type": "integer", "enum": [1, 2, 3, 4]},
            "resumo": {"type": "string"},
            "natureza": {"type": "string", "enum": NATUREZAS},
        },
        "required": ["indice", "relevante", "tipo", "cidade", "bairro", "gravidade", "resumo", "natureza"],
        "additionalProperties": False,
    }}},
    "required": ["itens"],
    "additionalProperties": False,
}


def ativo() -> bool:
    modo = os.getenv("CLASSIFICADOR", "auto").lower()
    return modo == "claude" or (modo == "auto" and bool(os.getenv("ANTHROPIC_API_KEY")))


_cliente: anthropic.AsyncAnthropic | None = None


def _cli() -> anthropic.AsyncAnthropic:
    global _cliente
    if _cliente is None:
        _cliente = anthropic.AsyncAnthropic()
    return _cliente


async def classificar_lote(noticias: list[dict]) -> list[dict | None]:
    """Recebe [{titulo, resumo}] e devolve, na mesma ordem, a classificação ou None (não relevante).
    Levanta exceção se a chamada falhar, para o chamador usar as regras."""
    saida: list[dict | None] = []
    for i in range(0, len(noticias), LOTE):
        parte = noticias[i:i + LOTE]
        texto = "\n\n".join(f"[{j}] {n['titulo']}\n{n['resumo'][:600]}" for j, n in enumerate(parte))
        resp = await _cli().beta.messages.create(
            model=MODELO,
            max_tokens=16000,
            system=_SISTEMA,
            messages=[{"role": "user", "content": f"Classifique as {len(parte)} notícias abaixo.\n\n{texto}"}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": _ESQUEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if resp.stop_reason != "end_turn":
            raise RuntimeError(f"resposta incompleta da IA: stop_reason={resp.stop_reason}")
        dados = json.loads(next(b.text for b in resp.content if b.type == "text"))
        por_indice = {it["indice"]: it for it in dados["itens"]}
        for j in range(len(parte)):
            it = por_indice.get(j)
            if not it or not it["relevante"]:
                saida.append(None)
                continue
            mun = MUN_POR_NOME.get(norm(it["cidade"] or ""))
            saida.append({"tipo": it["tipo"], "cvli": it["tipo"] in CVLI, "gravidade": it["gravidade"],
                          "natureza": it["natureza"], "desdobramento": it["natureza"] != OCORRENCIA,
                          "cidade": mun["nome"] if mun else None, "bairro": it["bairro"], "resumo": it["resumo"]})
        log.info("IA classificou %d notícias (%d relevantes)", len(parte), sum(1 for x in saida[-len(parte):] if x))
    return saida
