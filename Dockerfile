# Single-process Python API: frontend + watched-folder synthetic preview.
FROM python:3.12-alpine
WORKDIR /app
COPY funes/ ./funes/
COPY activity/ ./activity/
COPY frontend/ ./frontend/
COPY demo/ ./demo/
COPY demo-frames/ ./demo-frames/
COPY demo-thumbs/ ./demo-thumbs/
COPY tools/synthetic_scenarios.json ./tools/synthetic_scenarios.json
COPY docs/evidence/ ./docs/evidence/
COPY docs/architecture-overview.svg ./docs/architecture-overview.svg
COPY manifest.json ./manifest.json
RUN mkdir -p /data/incoming && chown 65532:65532 /data/incoming
USER 65532:65532
ENV FUNES_PREVIEW_CONTAINER=1
EXPOSE 8000
CMD ["python", "-m", "funes.server", "--host", "0.0.0.0", "--watch-dir", "/data/incoming"]
