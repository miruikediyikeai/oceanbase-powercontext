FROM python:3.13.9-slim
WORKDIR /app
COPY requirements.txt constraints.txt ./
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN --mount=type=secret,id=pip_ca_bundle --mount=type=cache,target=/root/.cache/pip \
    if [ -f /run/secrets/pip_ca_bundle ]; then export PIP_CERT=/run/secrets/pip_ca_bundle; fi; \
    pip install --index-url "$PIP_INDEX_URL" -r requirements.txt -c constraints.txt
COPY app.py serve_memory.py ./
COPY static ./static
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8812", "--workers", "1"]
