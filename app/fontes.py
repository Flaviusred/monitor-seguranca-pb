"""Fontes monitoradas. Para incluir uma nova, basta acrescentar um item em FEEDS."""
from urllib.parse import quote_plus, urlparse


def google_news(consulta: str) -> str:
    return ("https://news.google.com/rss/search?q=" + quote_plus(consulta)
            + "&hl=pt-BR&gl=BR&ceid=BR:pt-419")


# nome: rótulo exibido no status | url: endereço do RSS
# tipo: tipo padrão da fonte (itens agregados pelo Google Notícias são reclassificados pelo domínio)
FEEDS = [
    {"nome": "g1 Paraíba", "url": "https://g1.globo.com/rss/g1/pb/paraiba/", "tipo": "Portal de notícias"},
    {"nome": "ClickPB", "url": "https://www.clickpb.com.br/feed/", "tipo": "Portal de notícias"},
    {"nome": "MaisPB", "url": "https://www.maispb.com.br/feed", "tipo": "Portal de notícias"},
    {"nome": "Paraíba Online", "url": "https://paraibaonline.com.br/feed/", "tipo": "Portal de notícias"},
    {"nome": "Google Notícias · crimes letais", "agregador": True, "tipo": "Portal de notícias",
     "url": google_news('Paraíba (homicídio OR assassinado OR "morto a tiros" OR feminicídio OR latrocínio OR chacina) when:3d')},
    {"nome": "Google Notícias · patrimônio e drogas", "agregador": True, "tipo": "Portal de notícias",
     "url": google_news('Paraíba (assalto OR roubo OR furto OR "tráfico de drogas" OR "arma de fogo" OR "caixa eletrônico") when:3d')},
    {"nome": "Google Notícias · polícia", "agregador": True, "tipo": "Portal de notícias",
     "url": google_news('Paraíba (polícia OR preso OR "operação policial" OR "violência doméstica" OR estupro) when:3d')},
    {"nome": "Google Notícias · órgãos oficiais", "agregador": True, "tipo": "Portal de notícias",
     "url": google_news('site:pb.gov.br (polícia OR segurança OR preso OR apreensão OR operação OR homicídio) when:7d')},
]

REDES_SOCIAIS = ("instagram.com", "facebook.com", "x.com", "twitter.com", "youtube.com", "tiktok.com", "threads.net")


def tipo_por_dominio(url: str, padrao: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.endswith(".gov.br") or host.endswith(".jus.br") or host.endswith(".mp.br"):
        return "Portal oficial"
    if any(host == d or host.endswith("." + d) for d in REDES_SOCIAIS):
        return "Rede social"
    if "blog" in host:
        return "Blog"
    return padrao
