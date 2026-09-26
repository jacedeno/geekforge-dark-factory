FROM python:3.13-slim

# IANA time zone data is baked into the image; nothing is fetched at run time.
RUN pip install --no-cache-dir tzdata==2025.2

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /srv/app
COPY app/ /srv/app/

RUN useradd --system --no-create-home tablekeeper
USER tablekeeper

EXPOSE 8080
CMD ["python", "-m", "tablekeeper.server"]
