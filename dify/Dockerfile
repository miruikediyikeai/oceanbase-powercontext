FROM python:3.13.9-slim
WORKDIR /app
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt
COPY app.py serve_memory.py ./
COPY static ./static
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8812", "--workers", "1"]
