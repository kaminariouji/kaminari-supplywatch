# SupplyWatch — AMD ROCm image.
# For serving the optional LLM alert-drafting step on AMD GPUs (e.g. via vLLM),
# or running the pipeline on a ROCm host.
#
# Build: docker build -f docker/rocm.Dockerfile -t supplywatch-rocm .
# Run:   docker run --device=/dev/kfd --device=/dev/dri \
#          --security-opt seccomp=unconfined \
#          supplywatch-rocm scan --watchlist watchlist.demo.json --out demo/data/state.json
FROM rocm/pytorch:latest

WORKDIR /app
COPY agent/ agent/
COPY watchlist.demo.json watchlist.demo-v2.json ./
COPY demo/ demo/
COPY tests/ tests/

# Optional: install vLLM's ROCm build to serve an OpenAI-compatible endpoint
# inside the container (uncomment; large download):
# RUN pip install vllm

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-m", "agent.cli"]
CMD ["--help"]
