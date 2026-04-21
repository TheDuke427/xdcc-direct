FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/downloads

CMD ["sh", "-c", "echo 'nameserver 127.0.0.1' > /etc/resolv.conf && uvicorn main:app --host 0.0.0.0 --port 8080"]
