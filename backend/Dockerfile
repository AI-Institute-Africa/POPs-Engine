FROM python:3.12-slim

# tesseract is the independent second reader; libgl is not needed with opencv-headless
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
ARG INSTALL_DONUT=0
RUN if [ "$INSTALL_DONUT" = "1" ]; then \
      pip install --no-cache-dir '.[training]'; \
    else \
      pip install --no-cache-dir .; \
    fi
COPY . .
RUN pip install --no-cache-dir --no-deps . && useradd -r -u 1001 pops \
    && mkdir -p var /data/evidence && chown pops var /data/evidence
USER pops

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
