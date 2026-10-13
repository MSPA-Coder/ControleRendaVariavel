FROM postgres:17-alpine

# The upstream gosu binary currently fails the HIGH/CRITICAL image gate.
# Alpine's su-exec provides the same final-user exec behavior with a smaller
# C implementation. Fail closed if upstream changes the entrypoint contract.
RUN apk add --no-cache su-exec \
    && test "$(grep -F -c 'exec gosu postgres "$BASH_SOURCE" "$@"' /usr/local/bin/docker-entrypoint.sh)" -eq 1 \
    && sed -i 's/exec gosu postgres/exec su-exec postgres/' /usr/local/bin/docker-entrypoint.sh \
    && test "$(grep -F -c 'exec su-exec postgres "$BASH_SOURCE" "$@"' /usr/local/bin/docker-entrypoint.sh)" -eq 1 \
    && ! grep -q gosu /usr/local/bin/docker-entrypoint.sh \
    && rm -f /usr/local/bin/gosu \
    && ! command -v gosu \
    && su-exec postgres true
