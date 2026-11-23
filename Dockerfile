FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends iputils-ping net-tools \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY spider ./spider

ENV SPIDER_DATA_DIR=/app/data
EXPOSE 8080
CMD ["python", "-m", "spider"]
