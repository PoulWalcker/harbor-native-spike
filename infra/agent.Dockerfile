FROM alpine:3.24@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6
RUN apk add --no-cache python3=3.14.8-r0 py3-yaml=6.0.3-r2 bash=5.3.9-r1 && adduser -D -u 1000 author && mkdir -p /app/submission /submission && chown author /app/submission && chmod 700 /submission
COPY public/submission.py /opt/sapi-submission.py
WORKDIR /app
USER 1000
CMD ["sleep", "infinity"]
