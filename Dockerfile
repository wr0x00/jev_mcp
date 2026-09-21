FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY config.yaml .

ENV JEV_MCP_HOST=0.0.0.0 \
    JEV_MCP_PORT=8800 \
    PYTHONUNBUFFERED=1

EXPOSE 8800

CMD ["python", "server.py"]
