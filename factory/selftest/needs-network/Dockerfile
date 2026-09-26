# Deliberately broken: installs a package at build time, which a network-less build must refuse.
FROM docker.io/library/python:3.13-slim
WORKDIR /app
RUN pip install --no-cache-dir requests
COPY app.py .
CMD ["python3", "app.py"]
