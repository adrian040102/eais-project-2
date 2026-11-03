FROM python:3.10-slim-bookworm@sha256:5be5aaecb962a41567bc8041f1e9e3432cb085b037fe057f9a562630b2e4b370

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY eais-p2-kit/packages.txt eais-p2-kit/requirements.txt /tmp/eais-dependencies/
RUN python -m pip install --no-cache-dir --no-deps \
        -r /tmp/eais-dependencies/packages.txt \
        -r /tmp/eais-dependencies/requirements.txt \
    && python -m pip check \
    && useradd --create-home --uid 1000 --shell /bin/sh eais

USER eais
WORKDIR /workspace
CMD ["python", "--version"]
