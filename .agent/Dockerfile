FROM docker:29.8.1-cli AS docker-cli
FROM python:3.13-bookworm

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins /usr/local/libexec/docker/cli-plugins
COPY .agent/install-tools.py /tmp/install-tools.py
RUN python /tmp/install-tools.py && rm /tmp/install-tools.py
COPY scripts/requirements-validation.txt /tmp/requirements-validation.txt
RUN pip install --no-cache-dir --root-user-action=ignore -r /tmp/requirements-validation.txt
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /workspace
