FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=America/Santo_Domingo

WORKDIR /app

RUN pip install --no-cache-dir \
      fastapi==0.115.6 \
      "uvicorn[standard]==0.34.0" \
      httpx==0.28.1 \
      paramiko==3.5.0

COPY app.py /app/app.py
COPY autonomy.py /app/autonomy.py
COPY gitops.py /app/gitops.py

RUN useradd --create-home --uid 1000 corp \
 && mkdir -p /app/data \
 && chown -R corp:corp /app
USER corp

EXPOSE 8006
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8006", "--workers", "2"]
