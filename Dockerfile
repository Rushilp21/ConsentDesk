FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY consentdesk ./consentdesk
COPY web ./web
COPY server.py ./server.py
COPY scripts ./scripts
ENV HOST=0.0.0.0 PORT=8000 DATA_DIR=/app/data PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "server.py"]
