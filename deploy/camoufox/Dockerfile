# Supply an operator-reviewed image digest; no mutable default base tag.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG BASE_IMAGE
USER root
RUN printf '%s' "$BASE_IMAGE" | grep -Eq '@sha256:[0-9a-f]{64}$'
# The base must already contain Python3.11, Xvfb, Firefox shared libs and fonts.
# No apt/pip/browser downloads occur during this image build or at runtime.
ARG BROWSER_UID=10001
ARG BROWSER_GID=10001
RUN groupadd -g ${BROWSER_GID} crawler && useradd -u ${BROWSER_UID} -g ${BROWSER_GID} -m crawler
COPY --chown=0:0 payload/ /opt/camoufox-release/
RUN chmod -R a-w /opt/camoufox-release && mkdir -p /state/profiles /state/cache /state/tmp /state/home/.camoufox && chown -R crawler:crawler /state && chmod -R 700 /state
USER crawler
ENV CAMOUFOX_RELEASE_MANIFEST=/opt/camoufox-release/runtime.json
WORKDIR /opt/camoufox-release
ENTRYPOINT ["/opt/camoufox-release/venv-crawler/bin/python", "/opt/camoufox-release/deploy/camoufox/smoke.py"]
CMD ["/opt/camoufox-release/runtime.json", "--receipt", "/state/offline-smoke.json"]
