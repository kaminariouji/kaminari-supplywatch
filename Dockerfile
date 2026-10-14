# SupplyWatch — CPU image (full pipeline incl. deterministic alert template).
# Build:  docker build -t supplywatch .
# Run:    docker run -v "$PWD/demo:/app/demo" supplywatch \
#           scan --watchlist watchlist.demo.json --out demo/data/state.json
FROM python:3.12-slim

WORKDIR /app
COPY agent/ agent/
COPY watchlist.demo.json watchlist.demo-v2.json ./
COPY demo/ demo/
COPY tests/ tests/

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-m", "agent.cli"]
CMD ["--help"]
