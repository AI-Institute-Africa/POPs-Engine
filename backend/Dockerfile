FROM python:3.12-slim

# tesseract is the independent second reader; libgl is not needed with opencv-headless
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
COPY . .
RUN pip install --no-cache-dir --no-deps . && useradd -r -u 1001 pops && mkdir -p var && chown pops var
USER pops

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
