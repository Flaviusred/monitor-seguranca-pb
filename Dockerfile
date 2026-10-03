FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=America/Fortaleza \
    MONITOR_DB=/dados/monitor.db

WORKDIR /srv/monitor

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY web ./web

# Roda sem privilégios de administrador; /dados guarda o banco (volume persistente)
RUN useradd --system --uid 1000 monitor && mkdir -p /dados && chown monitor:monitor /dados
USER monitor
VOLUME ["/dados"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/saude', timeout=4)"

# Um único processo: a coleta periódica e o tempo real (SSE) vivem na memória dele
CMD ["uvicorn", "main:app", "--app-dir", "app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
