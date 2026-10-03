"""Classificação por regras: tipo de crime, gravidade, município e bairro.

Funciona sem nenhum serviço externo. Quando a classificação com IA está ativa (ver ia.py),
estas mesmas funções servem de reserva caso a chamada falhe.
"""
import json
import re
import unicodedata
from pathlib import Path


def sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def norm(s: str) -> str:
    return sem_acento(s).lower()


# ---------------------------------------------------------------- tipos de crime
# (nome, gravidade base 1-4, é CVLI?, padrões sobre o texto normalizado)
# A ordem importa: o primeiro tipo que casar vence.
TIPOS = [
    ("Feminicídio", 4, True, [r"feminicidi", r"mort[oa] pel[oa] (ex-)?(companheiro|marido|namorado|ex)\b"]),
    ("Latrocínio", 4, True, [r"latrocini", r"(mort[oa]|morre) (durante|em|apos|no|na) (um |uma )?(assalto|tentativa de assalto)",
                              r"morre apos ser baleado em assalto"]),
    ("Ataque a banco", 4, False, [r"explo\w* (de |do |dos |um |uma )?caix", r"ataque a (agencia|banco)", r"agencia bancaria",
                                  r"novo cangaco", r"explodem caixa"]),
    ("Homicídio", 3, True, [r"homicidi", r"assassinad", r"assassinato", r"mort[oa]s? a (tiros|facadas|pauladas|pedradas)", r"mort[oa]s? com .{0,25}(tiros|facadas)",
                            r"executad[oa]", r"chacina", r"encontrad[oa]s? mort[oa]", r"corpo e encontrado",
                            r"morre (apos ser |depois de ser )?baleado", r"mata (a )?(tiros|facadas)", r"\bmat(ou|ado|aram)\b", r"(jovem|homem|mulher|idos[oa]|crianca|adolescente|policial) (e )?mort[oa]\b",r"(suspeit|acusad)\w* de (matar|assassinar|executar)",
                            r"(participa\w*|autor) de (\w+ de )?(mortes|homicidios|assassinatos)"]),
    ("Tentativa de homicídio", 3, False, [r"tentativa de homicidio", r"\bbalead[oa]", r"esfaquead", r"atingid[oa] por (tiros?|disparos)",
                                         r"ataque a tiros", r"(tiros|disparos) contra"]),
    ("Crime sexual", 3, False, [r"estupr", r"abuso sexual",
                               r"\babus\w* (sexualmente )?d[eao] (uma |um )?(crianca|adolescente|menor|enteada|filha|sobrinha|vitima)",r"importunacao sexual", r"pedofil", r"exploracao sexual"]),
    ("Violência doméstica", 2, False, [r"violencia domestica", r"maria da penha", r"medida protetiva",
                                       r"agred\w* (a |sua )?(companheira|esposa|ex-companheira|ex-mulher|namorada)"]),
    ("Roubo", 2, False, [r"\broub", r"assalt", r"arrastao"]),
    ("Furto", 1, False, [r"\bfurt"]),
    ("Tráfico de drogas", 2, False, [r"trafico", r"\bdrogas?\b", r"maconha", r"cocaina", r"\bcrack\b", r"entorpecente"]),
    ("Apreensão de armas", 1, False, [r"arma de fogo", r"armas apreendidas", r"porte ilegal",
                                      r"apreend\w* (uma |duas |tres )?(arma|armas|espingarda|revolver|pistola|municoes)"]),
    ("Operação policial", 1, False, [r"operacao (policial|da policia|deflagrada)", r"deflagra\w* operacao", r"mandados? de (prisao|busca)",
                                     r"\bpres[oa]s?\b", r"\bprende", r"prisao em flagrante", r"capturad", r"foragid"]),
]
TIPOS_NOMES = [t[0] for t in TIPOS]
CVLI = {t[0] for t in TIPOS if t[2]}
GRAV_BASE = {t[0]: t[1] for t in TIPOS}
_TIPOS_RE = [(n, re.compile("|".join(p))) for n, _, _, p in TIPOS]

# Notícias que citam polícia/segurança mas não relatam um fato criminal
_EXCLUI = re.compile(r"concurso|edital|inscric|processo seletivo|aprovados|formatura|curso de formacao|"
                     r"campanha de vacinacao|licitacao|leilao|palestra|seminario|capacitacao|audiencia publica|"
                     r"projeto de lei|sanciona|\breforca\b|resumo d[ao]|o que se sabe|\bquem e\b|nega irregularidade|"
                     r"exoneracao|apontam dados|indices? de|estatistica|(sobem|caem|reducao|aumento|queda) (de )?\d|"
                     r"reducao d[aeo]s?|queda d[aeo]s?|acidente")
# Operações ligadas a política/eleições não entram como "operação policial" de segurança pública
_POLITICA = re.compile(r"eleitor|eleicao|eleicoes|eleitoral|candidat|deputad|vereador|senador|prefeit|secretari[oa] d|"
                       r"campanha|partido|policia federal|\bpf\b")
# Lugares de outros estados com "Paraíba" no nome
_OUTRA_PARAIBA = re.compile(r"vale do paraiba|paraiba do sul|sao jose dos campos|taubate")
# Agravantes de repercussão (somam 1 nível de gravidade)
_AGRAVA = re.compile(r"chacina|crianca|bebe|adolescente|idos[oa]|gravida|policia\w* (militar |civil |penal )?"
                     r"(e )?(morto|baleado|assassinado)|tiroteio|faccao|(duas|tres|quatro|cinco) pessoas|"
                     r"(dois|tres|quatro|cinco) (homens|jovens|mortos)|refens|em plena luz do dia|escola")


def tipo_crime(texto_norm: str) -> str | None:
    if _EXCLUI.search(texto_norm):
        return None
    for nome, rx in _TIPOS_RE:
        if rx.search(texto_norm):
            if nome == "Operação policial" and _POLITICA.search(texto_norm):
                return None
            return nome
    return None


def gravidade(tipo: str, texto_norm: str) -> int:
    g = GRAV_BASE.get(tipo, 1)
    if g >= 2 and _AGRAVA.search(texto_norm):
        g += 1
    return min(g, 4)


# ---------------------------------------------------------------- municípios
MUNICIPIOS = json.loads((Path(__file__).parent / "municipios_pb.json").read_text(encoding="utf-8"))
MUN_POR_NOME = {norm(m["nome"]): m for m in MUNICIPIOS}

# Nomes que também são sobrenomes, palavras comuns ou cidades de outros estados:
# só contam quando vêm depois de "em", "no", "na", "cidade de" etc.
_AMBIGUOS = {norm(n) for n in [
    "Sousa", "Monteiro", "Conde", "Patos", "Areia", "Lagoa", "Belém", "Esperança", "Prata", "Livramento", "Amparo",
    "Aparecida", "Triunfo", "Mari", "Pilar", "Condado", "Emas", "Mato Grosso", "Boa Vista", "Paulista", "Caraúbas",
    "Juarez Távora", "Pedro Régis", "Bernardino Batista", "Damião", "Desterro", "Diamante", "Malta", "Arara", "Lucena",
    "Imaculada", "Sossego", "Bom Jesus", "Bom Sucesso", "Santa Luzia", "Santa Helena", "São Domingos", "Riachão",
    "Logradouro", "Lastro", "Natuba", "Barra de São Miguel", "São Bento", "São José dos Ramos", "Cacimbas", "Matinhas",
    "Salgadinho", "Tavares", "Ingá", "Olivedos", "Serra Grande", "Bonito de Santa Fé", "Quixaba", "Coxixola",
]}
_PREP = r"(?:\bem|\bno|\bna|\bpara|\bpra|cidade de|municipio de|zona rural de|sertao de|regiao de)\s+"


def _rx_mun(nome: str) -> re.Pattern:
    alvo = re.escape(sem_acento(nome)).replace("\\'", "['’]")
    if norm(nome) in _AMBIGUOS:
        return re.compile(r"(?i:" + _PREP + r")" + r"(" + alvo + r")(?![\w])")
    return re.compile(r"(?<![\w])(" + alvo + r")(?![\w])")


# Nomes mais longos primeiro, para "São José de Piranhas" vencer "Piranhas"
_MUN_RE = [(m, _rx_mun(m["nome"])) for m in sorted(MUNICIPIOS, key=lambda m: -len(m["nome"]))]

# Bairros conhecidos que permitem inferir a cidade quando ela não é citada
BAIRROS_CIDADE = {
    "João Pessoa": ["Mangabeira", "Valentina", "Bancários", "Cristo Redentor", "Alto do Mateus", "Cruz das Armas", "Mandacaru",
                    "Manaíra", "Tambaú", "Cabo Branco", "Bessa", "Oitizeiro", "Jaguaribe", "Gramame", "Muçumagro", "Roger",
                    "Ilha do Bispo", "José Américo", "Ernesto Geisel", "Funcionários", "Costa e Silva", "Água Fria", "Rangel",
                    "Varadouro", "Altiplano", "Castelo Branco", "Padre Zé", "Jardim Veneza", "Grotão", "Colinas do Sul",
                    "Paratibe", "Torre", "Tambiá", "Treze de Maio", "Bairro dos Novais", "Jardim Cidade Universitária"],
    "Campina Grande": ["Bodocongó", "José Pinheiro", "Malvinas", "Catolé", "Alto Branco", "Jeremias", "Pedregal", "Mirante",
                       "Velame", "Serrotão", "Ramadinha", "Três Irmãs", "Presidente Médici", "Monte Castelo", "Santa Rosa",
                       "Distrito de Galante", "Jardim Continental", "Conceição", "Sandra Cavalcante", "Cruzeiro"],
}
_BAIRRO_RE = [(cid, b, re.compile(r"(?<![\w])" + re.escape(sem_acento(b)) + r"(?![\w])"))
              for cid, bs in BAIRROS_CIDADE.items() for b in bs]
_BAIRRO_EXPL = re.compile(r"[Bb]airro (?:d[eoa]s? )?((?:[A-Z][\w']+)(?: (?:d[eoa]s? |e )?[A-Z][\w']+){0,3})")
_PB = re.compile(r"\bparaiba\b|\bparaiban[oa]s?\b|\bPB\b|\bJP\b|grande joao pessoa", re.I)


def municipio(texto: str) -> dict | None:
    """Primeiro município da PB citado no texto (sem acento, caixa preservada)."""
    t = sem_acento(texto).replace("’", "'")
    t = re.sub(r"[Gg]rande Joao Pessoa|[Rr]egiao [Mm]etropolitana de Joao Pessoa", "", t)
    melhor = None
    for m, rx in _MUN_RE:
        achou = rx.search(t)
        if achou and (melhor is None or achou.start(1) < melhor[0]):
            melhor = (achou.start(1), m)
    if melhor:
        return melhor[1]
    return MUN_POR_NOME["joao pessoa"] if re.search(r"\b(?:em|de|na|no) JP\b", t) else None


# Créditos de foto/vídeo que as matérias trazem no resumo ("Reprodução / TV Cabo Branco") e que
# poderiam ser lidos como bairro
_CREDITOS = re.compile(r"TV Cabo Branco|TV Paraiba|Reproducao|Divulgacao|Arquivo pessoal|Redes sociais", re.I)


def bairro(texto: str) -> tuple[str | None, str | None]:
    """(bairro, cidade inferida pelo bairro ou None)."""
    t = _CREDITOS.sub(" ", sem_acento(texto))
    for cid, b, rx in _BAIRRO_RE:
        if rx.search(t):
            return b, cid
    achou = _BAIRRO_EXPL.search(t)
    return (achou.group(1), None) if achou else (None, None)


_IDADE = re.compile(r"\bde (\d{1,2}) anos\b(?! de (prisao|reclusao|pena|detencao))")


def detalhes(titulo: str, resumo: str) -> dict:
    """Detalhes que identificam um caso mesmo quando as manchetes são diferentes:
    bairros citados e idade da vítima ("homem de 50 anos")."""
    t = _CREDITOS.sub(" ", sem_acento(f"{titulo}. {(resumo or '')[:600]}"))
    bairros = {norm(b) for _, b, rx in _BAIRRO_RE if rx.search(t)}
    bairros |= {norm(m.group(1)) for m in _BAIRRO_EXPL.finditer(t)}
    idades = {m.group(1) for m in _IDADE.finditer(t.lower())}
    return {"b": sorted(bairros), "i": sorted(idades)}


def cita_paraiba(texto: str) -> bool:
    t = sem_acento(texto)
    return bool(_PB.search(t)) and not _OUTRA_PARAIBA.search(t.lower())


def classificar(titulo: str, resumo: str) -> dict | None:
    """Retorna a classificação ou None se a notícia não for de segurança pública na PB."""
    texto = f"{titulo}. {resumo}"
    tn = norm(texto)
    if _OUTRA_PARAIBA.search(tn) or _EXCLUI.search(norm(titulo)):
        return None
    tipo = tipo_crime(norm(titulo)) or tipo_crime(tn)  # o título manda; o resumo só desempata
    if not tipo:
        return None
    mun = municipio(titulo) or municipio(resumo)
    b, cid_bairro = bairro(texto)
    if not mun and cid_bairro:
        mun = MUN_POR_NOME[norm(cid_bairro)]
    if not mun and not cita_paraiba(texto):
        return None
    nat = natureza(titulo, tipo)
    return {"tipo": tipo, "cvli": tipo in CVLI, "gravidade": gravidade(tipo, tn), "natureza": nat,
            "desdobramento": nat != OCORRENCIA,
            "cidade": mun["nome"] if mun else None, "bairro": b, "resumo": None}


# ---------------------------------------------------------------- desdobramentos
# Prisão, investigação ou condenação ligada a um crime anterior. Para esses tipos, a notícia da prisão
# não é um crime novo: fica no feed, mas não entra nas contagens (a não ser que traga o próprio crime).
_TIPOS_COM_DESDOBRAMENTO = {"Homicídio", "Feminicídio", "Latrocínio", "Tentativa de homicídio", "Crime sexual",
                            "Ataque a banco", "Roubo"}
_PRISAO = re.compile(r"\bpres[oa]s?\b|\bprende|prisao|capturad|confess|indiciad|denunciad|condenad|julgament|"
                     r"investig|suspeit[oa]s? de|mandados?|foragid|identificad")
_FATO_NOVO = re.compile(r"mort[oa]s?\b|\bmorre|assassinad|executad|balead|esfaquead|encontrad[oa]s? mort|"
                        r"invad|refem|refens|explod|em flagrante|termina com|ao tentar|durante (o |um |uma )?(assalto|roubo)")


# "suspeito de assaltar", "acusado de matar", "condenado por": junto com uma prisão, referem-se a crime anterior
_SOBRE_CASO_ANTERIOR = re.compile(r"suspeit\w* de |acusad\w* de |condenad|confess|indiciad|denunciad")
_ATO_POLICIAL = re.compile(r"\bpres[oa]s?\b|\bprende|prisao|capturad|condenad|indiciad|denunciad|confess|foragid|"
                           r"mandados?|investig")
# Manchetes que resumem vários casos ("dois homicídios são registrados no fim de semana") repetem crimes
# que já entram um a um
_RESUMO_DE_CASOS = re.compile(r"(sao|foram) registrad|fim de semana (violento|sangrento)|balanco|"
                              r"\b(dois|duas|tres|quatro|cinco|seis|\d+) (homicidios|assassinatos|mortes|crimes|assaltos|roubos)\b")


# Justiça: condenação, júri, sentença. Nunca é um crime acontecendo agora, mesmo que a manchete diga "matou"
_JUSTICA = re.compile(r"condenad|julgament|\bjuri\b|tribunal|sentenc|absolvid|\breu\b|anos de (prisao|reclusao)|"
                      r"pena de|audiencia|denuncia (do|pelo) mp|ministerio publico denuncia|vai a juri|\bjulga\b")
# Retrospectiva de casos antigos
_RETROSPECTIVA = re.compile(r"\bha \d+ anos|completa \d+ anos|relembr|\d+ anos (depois|apos)|anos depois|caso arquivado|"
                            r"reabert|sem solucao")

OCORRENCIA = "ocorrência"
PRISAO = "prisão/investigação"
JUSTICA = "condenação/julgamento"
RESUMO = "resumo/retrospectiva"


def natureza(titulo: str, tipo: str) -> str:
    """O que a notícia relata: o crime em si (ocorrência) ou um desdobramento de caso anterior.
    Só ocorrências entram nos números do painel."""
    t = norm(titulo)
    if _JUSTICA.search(t):
        return JUSTICA
    if _RESUMO_DE_CASOS.search(t) or _RETROSPECTIVA.search(t):
        return RESUMO
    if tipo not in _TIPOS_COM_DESDOBRAMENTO or not _ATO_POLICIAL.search(t) or "em flagrante" in t:
        return OCORRENCIA
    if _SOBRE_CASO_ANTERIOR.search(t) and not re.search(r"\bmorre|\bmort[oa]\b", t):
        return PRISAO
    return OCORRENCIA if _FATO_NOVO.search(t) else PRISAO


def desdobramento(titulo: str, tipo: str) -> bool:
    return natureza(titulo, tipo) != OCORRENCIA


# ---------------------------------------------------------------- semelhança entre notícias (agrupamento)
_STOP = set("de da do das dos em no na nos nas e o a os as um uma por com para apos durante sem contra que se "
            "ser sao foi e pela pelo apos ate mais".split())
# Palavras genéricas de notícia policial e nomes de municípios não ajudam a dizer se é o MESMO fato
_GENERICAS = set("""morto morta mortos mortas morre morreu tiros tiro assassinado assassinada assassinato preso presa presos
presas prende prendem prisao policia policial policiais suspeito suspeita suspeitos homem homens mulher mulheres jovem jovens
adolescente pessoa pessoas paraiba sertao litoral cariri agreste brejo regiao interior cidade zona rural bairro video imagens
mostram sobre entre noticia noticias diz afirma investigado investigada operacao civil militar federal trafico drogas droga
entorpecente apreende apreendido apreendida apreensao assalto assaltos assaltantes roubo roubos roubar roubado furto furtos
estupro estuprar homicidio
homicidios tentativa crime crimes criminosos dois duas tres quatro cinco anos idade apos durante grande norte""".split())
# Nomes completos dos municípios (e "Grande João Pessoa") saem do título antes de comparar;
# palavras soltas como "dentro" ou "lagoa" continuam valendo
_RX_MUNICIPIOS = re.compile(r"\b(?:grande joao pessoa|" + "|".join(
    re.escape(norm(m["nome"]).replace("'", " ")) for m in sorted(MUNICIPIOS, key=lambda m: -len(m["nome"]))) + r")\b")
# Sinônimos frequentes nas manchetes, reduzidos ao mesmo radical
_SINONIMOS = {"invas": "invad", "domic": "casa", "resid": "casa", "decap": "degol", "motoc": "moto"}
_GENERICAS |= {"detido", "detida", "detidos", "detidas"}
# Sufixo de veículo que o Google Notícias e alguns portais põem no título: "... - G1", "... - Paraíba - Notícia"
_SUFIXO_FONTE = re.compile(r"(?:\s+[-–|]\s+[^-–|]{2,40}){1,2}$")


def limpa_titulo(titulo: str) -> str:
    return _SUFIXO_FONTE.sub("", titulo).strip() if len(titulo) > 50 else titulo


def tokens(s: str) -> set[str]:
    """Palavras distintivas do título, reduzidas a 5 letras (invadida → invad). Números com 2+ dígitos
    contam (ex.: "20 tiros"), exceto idades ("20 anos"), que se repetem demais entre casos diferentes."""
    base = norm(limpa_titulo(s)).replace("'", " ")
    # assinatura da manchete inteira: títulos iguais (a menos de "-PB", pontuação etc.) são sempre o mesmo fato
    out = {"=" + " ".join(w for w in re.split(r"[^a-z0-9]+", base) if w and w not in _STOP and w not in ("pb", "paraiba"))}
    t = _RX_MUNICIPIOS.sub(" ", base)
    palavras = re.split(r"[^a-z0-9]+", t)
    for i, w in enumerate(palavras):
        if w in _STOP or w in _GENERICAS:
            continue
        if w.isdigit():
            if len(w) >= 2 and (i + 1 >= len(palavras) or palavras[i + 1] not in ("anos", "ano")):
                out.add(w)
        elif len(w) > 3:
            out.add(_SINONIMOS.get(w[:5], w[:5]))
    return out


def mesmo_fato(a: set[str], b: set[str], mesmo_tipo: bool) -> float:
    """Pontuação 0-1 de que dois conjuntos de palavras descrevem o mesmo fato (0 = não).
    Números em comum valem em dobro. Tipos diferentes (ex.: o crime e a prisão do suspeito)
    exigem semelhança maior."""
    if any(w.startswith("=") for w in a & b):
        return 1.0  # mesma manchete
    a = {w for w in a if not w.startswith("=")}
    b = {w for w in b if not w.startswith("=")}
    if not a or not b:
        return 0.0
    comum = a & b
    peso = len(comum) + sum(1 for w in comum if w.isdigit())
    score = min(1.0, peso / min(len(a), len(b)))
    minimo, limiar = (2, 0.5) if mesmo_tipo else (3, 0.6)
    return score if peso >= minimo and score >= limiar else 0.0
