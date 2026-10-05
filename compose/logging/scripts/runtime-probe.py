#!/usr/bin/env python3
"""Probe only the disposable ELK fixture created by scripts/smoke-logging.py.

Credentials are read from Docker secret files. The orchestrator supplies the
network, CA, collector certificate and fresh data volumes; this script neither
starts containers nor changes host configuration. stdout is one JSON result.
"""

import argparse
import base64
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid

import admin


ES = "https://elasticsearch:9200"
KIBANA = "https://kibana:5601"
INGEST = "https://logstash:8443"
REPOSITORY = "platform-filesystem"
CA = "/tls/ca.crt"


class ProbeError(RuntimeError):
    """A failed assertion whose message is safe to include in the report."""


def require(condition, message):
    if not condition:
        raise ProbeError(message)


def valid_identity(value):
    if not re.fullmatch(r"[a-f0-9]{12,32}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", value):
        raise argparse.ArgumentTypeError("identity must be a lowercase UUID or 12-32 hexadecimal characters")
    return value


def request(method, path, data=None, *, endpoint=ES, user="elastic",
            secret="elastic_password", expected=(200,), wrong_password=False):
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "kbn-xsrf": "isolated-runtime-smoke"}
    if user is not None:
        value = "deliberately-invalid-password" if wrong_password else admin.password(secret)
        token = base64.b64encode(f"{user}:{value}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(endpoint + path, data=body, headers=headers, method=method)
    context = ssl.create_default_context(cafile=CA)
    try:
        with urllib.request.urlopen(req, context=context, timeout=90) as response:
            status, content = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, content = exc.code, exc.read()
    require(status in expected, f"{method} {path}: expected HTTP {expected}, received {status}")
    if status >= 400 or not content:
        return {}
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # Logstash HTTP input responds with plain text after accepting JSON.
        require(endpoint == INGEST, f"{method} {path}: expected a JSON response")
        return {}


def tls_context(client_certificate):
    context = ssl.create_default_context(cafile=CA)
    # TLS 1.2 completes mutual authentication during wrap_socket. TLS 1.3 can
    # defer the no-certificate alert until application I/O; that is unsuitable
    # for this handshake-only assertion. Real Filebeat uses its own defaults.
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    if client_certificate:
        context.load_cert_chain("/tls/collector/client.crt", "/tls/collector/client.key")
    return context


def beats_tls():
    with socket.create_connection(("logstash", 5044), timeout=10) as raw:
        with tls_context(True).wrap_socket(raw, server_hostname="logstash") as connection:
            require(connection.version() == "TLSv1.2", "Beats positive TLS handshake did not complete")
    try:
        with socket.create_connection(("logstash", 5044), timeout=10) as raw:
            with tls_context(False).wrap_socket(raw, server_hostname="logstash"):
                pass
    except ssl.SSLError as exc:
        # A refused TCP connection, timeout, untrusted server, or hostname
        # mismatch is not evidence of client certificate enforcement.
        require(exc.reason in {
            "SSLV3_ALERT_HANDSHAKE_FAILURE", "SSLV3_ALERT_BAD_CERTIFICATE",
            "TLSV1_ALERT_UNKNOWN_CA", "TLSV13_ALERT_CERTIFICATE_REQUIRED",
            "SSLV3_ALERT_CERTIFICATE_UNKNOWN", "TLSV1_ALERT_ACCESS_DENIED",
        }, "Beats negative check failed without a client-authentication TLS alert")
        return {"clientCertificateAccepted": True, "missingClientCertificateRejected": True,
                "handshakeProtocol": "TLSv1.2"}
    raise ProbeError("Beats accepted a TLS handshake without a client certificate")


def marker_counts(identity, *, prefix="platform-logs-*", wait_seconds=120):
    queries = {
        "http": {"term": {"smoke_id.keyword": identity + "-http"}},
        "beats": {"match_phrase": {"message": identity + "-beats"}},
    }
    deadline = time.monotonic() + wait_seconds
    counts = {}
    while True:
        for kind, query in queries.items():
            response = request("POST", f"/{prefix}/_search?ignore_unavailable=true", {
                "query": query, "size": 10, "track_total_hits": True,
                "_source": ["message", "smoke_id"],
            })
            hits = response.get("hits", {})
            total = hits.get("total", {})
            require(total.get("relation") == "eq", "Marker query did not return an exact hit count")
            counts[kind] = total.get("value", 0)
            for hit in hits.get("hits", []):
                source = hit.get("_source", {})
                match = (source.get("smoke_id") == identity + "-http" if kind == "http"
                         else identity + "-beats" in source.get("message", ""))
                require(match, "Marker search matched an unexpected event")
        if all(counts.get(kind, 0) >= 1 for kind in queries):
            return counts
        if time.monotonic() >= deadline:
            raise ProbeError("HTTP or Filebeat marker did not reach Elasticsearch within the deadline")
        time.sleep(2)


def writer_permissions(identity):
    writer = {"user": "logstash_writer", "secret": "logstash_password"}
    index = "platform-logs-permissions-" + identity
    document = identity + "-writer"
    body = {"message": "isolated writer authorization probe", "smoke_id": document,
            "@timestamp": datetime.now(timezone.utc).isoformat()}
    request("PUT", f"/{index}/_create/{document}", body, expected=(201,), **writer)
    checks = (
        ("GET", f"/{index}/_search", None),
        ("POST", f"/{index}/_update/{document}", {"doc": {"message": "must not overwrite"}}),
        ("PUT", f"/{index}/_doc/{document}", {"message": "must not overwrite"}),
        ("DELETE", f"/{index}/_doc/{document}", None),
        ("DELETE", f"/{index}", None),
        ("PUT", f"/outside-platform-{identity}/_create/{document}", body),
        ("PUT", "/_index_template/forbidden-" + identity, {"index_patterns": ["forbidden-*"]}),
        ("PUT", "/_ilm/policy/forbidden-" + identity, {"policy": {"phases": {"hot": {"actions": {}}}}}),
        ("GET", "/_security/user", None),
    )
    for method, path, data in checks:
        request(method, path, data, expected=(403,), **writer)
    stored = request("GET", f"/{index}/_doc/{document}")
    require(stored.get("_source") == body, "Writer negative checks changed or removed the original event")
    return {"create": True, "deniedOperations": len(checks)}


def seed(identity):
    request("GET", "/", user=None, expected=(401,))
    request("POST", "/", {"message": "must not be accepted"}, endpoint=INGEST,
            user=None, expected=(401,))
    request("POST", "/", {"message": "must not be accepted"}, endpoint=INGEST,
            user="collector", wrong_password=True, expected=(401,))
    request("GET", "/api/data_views", endpoint=KIBANA, user=None, expected=(401,))
    request("POST", "/", {"message": "isolated HTTP ingestion probe", "smoke_id": identity + "-http"},
            endpoint=INGEST, user="collector", secret="ingest_password", expected=(200, 201, 202))
    permissions = writer_permissions(identity)
    mutual_tls = beats_tls()
    view_id = "smoke-" + identity
    view = request("POST", "/api/data_views/data_view", {
        "data_view": {"id": view_id, "title": "platform-logs-*", "timeFieldName": "@timestamp"},
    }, endpoint=KIBANA)
    require(view.get("data_view", {}).get("id") == view_id, "Kibana did not create the expected Data View")
    return {"identity": identity, "authorization": permissions, "beatsTLS": mutual_tls,
            "dataView": view_id, "unauthenticatedRequestsRejected": True}


def verify(identity):
    counts = marker_counts(identity)
    status = request("GET", "/api/status", endpoint=KIBANA)
    require(status.get("status", {}).get("overall", {}).get("level") == "available",
            "Kibana authenticated status is not available")
    view = request("GET", "/api/data_views/data_view/smoke-" + identity, endpoint=KIBANA).get("data_view", {})
    require(view.get("title") == "platform-logs-*" and view.get("timeFieldName") == "@timestamp",
            "Kibana Data View was not preserved")
    templates = request("GET", "/_index_template/platform-logs").get("index_templates", [])
    require(len(templates) == 1, "Logging index template is absent or ambiguous")
    template = templates[0].get("index_template", {})
    require(template.get("index_patterns") == ["platform-logs-*"], "Logging index template scope changed")
    policy = request("GET", "/_ilm/policy/platform-logs-retention").get("platform-logs-retention", {})
    deletion = policy.get("policy", {}).get("phases", {}).get("delete", {})
    require(deletion.get("min_age") == "14d" and "delete" in deletion.get("actions", {}),
            "Logging ILM retention policy is not 14 days")
    settings = request("GET", "/platform-logs-*/_settings?flat_settings=true")
    require(settings and all(value.get("settings", {}).get("index.lifecycle.name") == "platform-logs-retention"
                             for value in settings.values()), "An active logging index is missing the ILM policy")
    slm = request("GET", "/_slm/policy/platform-daily").get("platform-daily", {}).get("policy", {})
    require(slm.get("schedule") == "0 30 2 * * ?" and slm.get("repository") == REPOSITORY
            and slm.get("config", {}).get("include_global_state") is True
            and slm.get("retention") == {"expire_after": "14d", "min_count": 3, "max_count": 30},
            "Daily snapshot policy differs from the configured schedule and retention")
    return {"identity": identity, "markers": counts, "kibanaAvailable": True,
            "dataViewPreserved": True, "ilmRetentionDays": 14, "slmPolicyVerified": True,
            "indexCount": len(settings)}


def snapshot():
    request("POST", "/platform-logs-*/_refresh")
    count = request("GET", "/platform-logs-*/_count").get("count", 0)
    require(count >= 3, "Expected HTTP, Beats and writer fixture events before snapshot")
    name = "platform-smoke-" + uuid.uuid4().hex
    result = request("PUT", f"/_snapshot/{REPOSITORY}/{name}?wait_for_completion=true", {
        "include_global_state": True,
        "metadata": {"purpose": "platform logging isolated runtime smoke", "expected_log_documents": count},
    }).get("snapshot", {})
    shards = result.get("shards", {})
    total = shards.get("total", 0)
    require(result.get("state") == "SUCCESS" and type(total) is int and total > 0
            and shards.get("failed") == 0 and shards.get("successful") == total,
            "Snapshot did not complete successfully")
    return {"snapshot": name, "state": "SUCCESS", "expectedLogDocuments": count,
            "successfulShards": shards.get("successful")}


def assert_empty_logs():
    existing = request("GET", "/_resolve/index/platform-logs-*?expand_wildcards=all")
    require(not any(existing.get(kind) for kind in ("indices", "aliases", "data_streams")),
            "Restore target already contains logging indices, aliases or data streams")


def restore(snapshot_name, identity):
    assert_empty_logs()
    request("PUT", f"/_snapshot/{REPOSITORY}", {
        "type": "fs", "settings": {"location": "/mnt/snapshots", "readonly": True},
    })
    repository = request("GET", f"/_snapshot/{REPOSITORY}").get(REPOSITORY, {})
    require(str(repository.get("settings", {}).get("readonly")).lower() == "true",
            "Restore repository is not read-only")
    snapshots = request("GET", f"/_snapshot/{REPOSITORY}/{snapshot_name}").get("snapshots", [])
    require(len(snapshots) == 1 and snapshots[0].get("state") == "SUCCESS", "Snapshot is not a completed SUCCESS backup")
    metadata = snapshots[0].get("metadata", {})
    expected = metadata.get("expected_log_documents")
    require(metadata.get("purpose") == "platform logging isolated runtime smoke"
            and type(expected) is int and expected >= 3, "Snapshot lacks isolated-fixture document count evidence")
    prefix = "drill-" + identity
    # Exercise the same operational implementation documented for operators.
    # Suppress its informational output so the outer CLI emits one JSON record.
    with redirect_stdout(io.StringIO()):
        admin.restore_logs(snapshot_name, prefix)
    index_pattern = prefix + "-platform-logs-*"
    counts = marker_counts(identity, prefix=index_pattern)
    total = request("GET", f"/{index_pattern}/_count").get("count")
    require(total == expected, "Restored document count differs from snapshot source count")
    settings = request("GET", f"/{index_pattern}/_settings?flat_settings=true")
    require(settings and all(not value.get("settings", {}).get("index.lifecycle.name")
                             for value in settings.values()), "Restored logs still have an active ILM policy")
    try:
        with redirect_stdout(io.StringIO()):
            admin.restore_logs(snapshot_name, prefix)
    except SystemExit as exc:
        require("Restore prefix is already in use" in str(exc), "Repeat restore failed for an unexpected reason")
    else:
        raise ProbeError("Repeat restore did not reject an occupied prefix")
    # A logs-only restore must leave original names and privileged identities
    # absent, even though the snapshot also includes security feature state.
    assert_empty_logs()
    users = request("GET", "/_security/user")
    require("logstash_writer" not in users, "Logs-only restore unexpectedly restored the writer identity")
    return {"identity": identity, "snapshot": snapshot_name, "markers": counts,
            "restoredDocuments": total, "repositoryReadOnly": True,
            "ilmRemoved": True, "occupiedPrefixRejected": True, "securityStateNotRestored": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("seed", "verify", "restore"):
        sub = commands.add_parser(command)
        sub.add_argument("--identity", type=valid_identity, required=True)
        if command == "restore":
            sub.add_argument("--snapshot", type=admin.valid_name, required=True)
    commands.add_parser("snapshot")
    args = parser.parse_args()
    try:
        if args.command == "seed":
            result = seed(args.identity)
        elif args.command == "verify":
            result = verify(args.identity)
        elif args.command == "snapshot":
            result = snapshot()
        else:
            result = restore(args.snapshot, args.identity)
        print(json.dumps({"status": "passed", "command": args.command, **result}, sort_keys=True))
        return 0
    except (ProbeError, OSError, urllib.error.URLError, SystemExit) as exc:
        # Never include response bodies, headers, secret contents or URLs that
        # could hold credentials. ProbeError is constructed only from constants.
        message = str(exc) if isinstance(exc, ProbeError) else type(exc).__name__ + ": check fixture connectivity, TLS and logs"
        print(json.dumps({"status": "failed", "command": args.command, "error": message}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
