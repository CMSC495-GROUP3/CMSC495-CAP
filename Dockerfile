# The API image. Build context is the repository root (docker-compose.yml sets
# it) so the whole policy_assistant package is copied in. The web app has its
# own Dockerfile under web/.

FROM python:3.14-slim

WORKDIR /app

# Dependencies first — cached layer, rebuilt only when requirements change
COPY requirements/base.txt requirements/api.txt requirements/
RUN pip install --no-cache-dir -r requirements/api.txt

COPY policy_assistant/ policy_assistant/

EXPOSE 8000

# --proxy-headers makes request.client the real caller rather than the Nginx
# container, which the rate limiter keys on. Only the Compose network can reach
# this port, so trusting every proxy address is safe.
CMD ["uvicorn", "policy_assistant.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
