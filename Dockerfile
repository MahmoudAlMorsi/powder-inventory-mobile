FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-ara \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pp.py ./pp.py
COPY mobile ./mobile

EXPOSE 8765
CMD ["python", "mobile/server.py"]
