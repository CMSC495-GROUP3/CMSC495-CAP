# Build context is the project root (set in docker-compose.yml) so this image
# can reach both backend/ and src/.

FROM python:3.11-slim

WORKDIR /app

# Dependencies first — cached layer, rebuilt only when requirements change
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

# The RAG pipeline. Backend modules add ../src to sys.path at import time, which
# resolves to this location inside the container.
COPY src/ /src

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
