FROM python:3.10-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .
# Never bake real credentials into the image; configure via env at runtime.
ENV PYTHONUNBUFFERED=1 \
    PORT=8010
EXPOSE 8010
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,os;urllib.request.urlopen('http://localhost:'+os.environ.get('PORT','8010')+'/health',timeout=4)"
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8010}"]
