FROM python:3.13-bookworm@sha256:cc55e4ded9c95f6ce2133b3d096db43f214d280c2a8af65d83e246c8fa7d168c AS maps
WORKDIR /build
COPY analyzer/*.py ./analyzer/
COPY requirements.lock sample/tests.json ./
RUN git init -q /sqlglot \
    && git -C /sqlglot fetch --depth=1 https://github.com/tobymao/sqlglot.git eb11aec4be8cb2122afe713a4816662c3f0fb2fa \
    && git -C /sqlglot checkout -q --detach FETCH_HEAD \
    && mkdir /maps \
    && python -B -m analyzer.dependency_graph /sqlglot /maps/dependencies.json
# Re-derive the selector's static tables from the pinned checkout; the image
# uses this copy (identical to the checked-in tables when nothing drifted).
RUN python -B analyzer/test_selector.py --refresh /sqlglot tests.json analyzer/test_selector.py
# Coverage is optional positive evidence. Keep the dependency artifact if the
# locked test environment cannot be installed or coverage cannot checkpoint.
RUN if python -m pip --disable-pip-version-check --timeout 10 --retries 1 install --no-cache-dir -r requirements.lock; then \
        PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTEST_ADDOPTS="-p no:cacheprovider -p pytest_timeout --timeout=20" \
            python -B -m analyzer.coverage_map /sqlglot tests.json /maps/coverage.json --timeout 120 \
            || echo 'Coverage collection unavailable; runtime will fall back' >&2; \
    else \
        echo 'Locked coverage dependencies unavailable; runtime will fall back' >&2; \
    fi

# Match the scorer's original Linux/Python runtime.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
WORKDIR /app
COPY selector.py ./
COPY analyzer/*.py ./analyzer/
COPY --from=maps /maps/ ./maps/
COPY --from=maps /build/analyzer/test_selector.py ./analyzer/test_selector.py
# Selection policy baked into the image: balanced (measured default),
# safe, or fallback (the validated full-suite behaviour). Build with
# --build-arg TESTRADAR_POLICY=fallback to restore it. See
# docs/SELECTION_POLICY.md.
ARG TESTRADAR_POLICY=balanced
ENV PYTHONDONTWRITEBYTECODE=1 TESTRADAR_POLICY=${TESTRADAR_POLICY}
ENTRYPOINT ["python", "/app/selector.py"]
