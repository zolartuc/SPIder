# Stage 1: work out which git commit is being built (the final image has no .git).
FROM python:3.12-slim AS buildinfo
WORKDIR /src
COPY . .
RUN python spider/buildinfo.py .git spider > /build.json

# Stage 2: SPIder itself.
FROM python:3.12-slim

# No apt-get: everything SPIder needs is pure Python or ships as a prebuilt wheel,
# so the image builds on any Pi without downloading Debian packages.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY spider ./spider
COPY --from=buildinfo /build.json ./spider/build.json

ENV SPIDER_DATA_DIR=/app/data
EXPOSE 8080
CMD ["python", "-m", "spider"]
