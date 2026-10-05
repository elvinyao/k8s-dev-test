#!/usr/bin/env python3
"""Explicit one-shot bootstrap, smoke verification, snapshot and restore actions."""

import argparse
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import ssl
import time
import urllib.error
import urllib.request
import uuid


ES = "https://elasticsearch:9200"


def password(name):
    value = (Path("/run/secrets") / name).read_text().strip()
    if len(value) < 32:
        raise SystemExit(f"Secret {name} must contain at least 32 characters.")
    return value


def request(method, path, data=None, *, endpoint=ES, user="elastic", secret="elastic_password"):
    token = base64.b64encode(f"{user}:{password(secret)}".encode()).decode()
    headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json"}
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(endpoint + path, data=body, headers=headers, method=method)
    context = ssl.create_default_context(cafile="/tls/ca.crt")
    with urllib.request.urlopen(req, context=context, timeout=90) as response:
        result = response.read()
    try:
        return json.loads(result) if result else {}
    except json.JSONDecodeError:
        return {"accepted": True}


def bootstrap():
    for attempt in range(36):
        try:
            health = request("GET", "/_cluster/health?wait_for_status=yellow&timeout=5s")
            if not health.get("timed_out") and health.get("status") in ("yellow", "green"):
                break
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(5)
    else:
        raise SystemExit("Elasticsearch did not become ready with the supplied administrator secret.")

    request("PUT", "/_ilm/policy/platform-logs-retention", {
        "policy": {"phases": {"hot": {"actions": {}}, "delete": {"min_age": "14d", "actions": {"delete": {}}}}}
    })
    request("PUT", "/_index_template/platform-logs", {
        "index_patterns": ["platform-logs-*"],
        "priority": 250,
        "template": {
            "settings": {
                "number_of_shards": 1,
                "number_of_replicas": 0,
                "index.lifecycle.name": "platform-logs-retention",
                "index.mapping.total_fields.limit": 1000,
            },
            "mappings": {"properties": {"@timestamp": {"type": "date"}, "message": {"type": "match_only_text"}}},
        },
    })
    request("PUT", "/_security/role/platform_logstash_writer", {
        "cluster": ["monitor"],
        "indices": [{"names": ["platform-logs-*"], "privileges": ["create_doc", "create_index", "auto_configure"]}],
    })
    request("PUT", "/_security/user/logstash_writer", {
        "password": password("logstash_password"),
        "roles": ["platform_logstash_writer"],
        "full_name": "Platform Logstash ingestion service",
    })
    request("POST", "/_security/user/kibana_system/_password", {"password": password("kibana_password")})
    request("PUT", "/_security/role/platform_log_reader", {
        "cluster": [],
        "indices": [{"names": ["platform-logs-*"], "privileges": ["read", "view_index_metadata"]}],
    })
    request("PUT", "/_snapshot/platform-filesystem", {"type": "fs", "settings": {"location": "/mnt/snapshots", "compress": True}})
    request("POST", "/_snapshot/platform-filesystem/_verify")
    request("PUT", "/_slm/policy/platform-daily", {
        "schedule": "0 30 2 * * ?",
        "name": "<platform-daily-{now/d}>",
        "repository": "platform-filesystem",
        "config": {"include_global_state": True},
        "retention": {"expire_after": "14d", "min_count": 3, "max_count": 30},
    })
    print("Bootstrap complete: scoped writer, kibana_system credential, reader role, 14-day log retention and daily snapshots.")
    print("Existing secret files and CA keys were not changed. The same supplied service passwords were applied.")


def check():
    health = request("GET", "/_cluster/health")
    print(json.dumps({name: health.get(name) for name in ("cluster_name", "status", "number_of_nodes", "unassigned_shards")}, indent=2))
    if health.get("status") == "red":
        raise SystemExit("Cluster health is red.")
    print("This command checks Elasticsearch only; smoke additionally verifies ingestion.")


def smoke():
    identity = str(uuid.uuid4())
    request("POST", "/", {"message": "platform logging smoke check", "smoke_id": identity},
            endpoint="https://logstash:8443", user="collector", secret="ingest_password")
    for attempt in range(30):
        result = request("POST", "/platform-logs-*/_search?ignore_unavailable=true", {
            "query": {"match_phrase": {"smoke_id": identity}}, "size": 1,
        })
        if result.get("hits", {}).get("hits"):
            print(f"PASS: authenticated HTTPS ingestion reached Elasticsearch; smoke_id={identity}")
            return
        time.sleep(2)
    raise SystemExit("Smoke event was accepted by Logstash but was not indexed within 60 seconds.")


def valid_name(value):
    if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,100}", value):
        raise argparse.ArgumentTypeError("Use 1-101 lowercase letters, digits, dots, underscores or hyphens, starting with a letter.")
    return value


def restore_logs(snapshot, prefix):
    # A closed index may otherwise be overwritten by Elasticsearch's restore
    # API. Include hidden/closed indices and aliases when reserving the prefix.
    existing = request("GET", f"/_resolve/index/{prefix}-*?expand_wildcards=all")
    if any(existing.get(kind) for kind in ("indices", "aliases", "data_streams")):
        raise SystemExit("Restore prefix is already in use; choose a new prefix. No restore was requested.")
    result = request("POST", f"/_snapshot/platform-filesystem/{snapshot}/_restore?wait_for_completion=true", {
        "indices": "platform-logs-*",
        "include_global_state": False,
        "feature_states": ["none"],
        "rename_pattern": "(.+)",
        "rename_replacement": prefix + "-$1",
        "include_aliases": False,
        "index_settings": {"number_of_replicas": 0},
        "ignore_index_settings": ["index.lifecycle.name"],
    })
    print(json.dumps(result, indent=2))
    shards = result.get("snapshot", {}).get("shards", {})
    total = shards.get("total", 0)
    if total < 1 or shards.get("failed") != 0 or shards.get("successful") != total:
        raise SystemExit("Restore completion was not proven; inspect recovery and shard state before retrying.")
    print("Restored logs under a new prefix without restoring security state or Kibana state.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("bootstrap", "check", "smoke", "snapshot-create", "snapshot-list"):
        commands.add_parser(name)
    restore = commands.add_parser("restore-logs")
    restore.add_argument("--snapshot", type=valid_name, required=True)
    restore.add_argument("--prefix", type=valid_name, required=True, help="New index prefix, e.g. drill-20260926")
    args = parser.parse_args()
    if args.command == "bootstrap":
        bootstrap()
    elif args.command == "check":
        check()
    elif args.command == "smoke":
        smoke()
    elif args.command == "snapshot-create":
        name = "platform-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        result = request("PUT", f"/_snapshot/platform-filesystem/{name}?wait_for_completion=true", {
            "include_global_state": True,
            "metadata": {"purpose": "platform logging backup"},
        })
        snapshot = result.get("snapshot", {})
        print(json.dumps({"snapshot": name, "state": snapshot.get("state"), "shards": snapshot.get("shards")}, indent=2))
        if snapshot.get("state") != "SUCCESS":
            raise SystemExit("Snapshot did not complete successfully; inspect the snapshot API before claiming a backup.")
    elif args.command == "snapshot-list":
        result = request("GET", "/_snapshot/platform-filesystem/_all")
        print(json.dumps([{"snapshot": s["snapshot"], "state": s["state"], "start_time": s.get("start_time")} for s in result["snapshots"]], indent=2))
    elif args.command == "restore-logs":
        restore_logs(args.snapshot, args.prefix)


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"API request failed: HTTP {exc.code}; inspect service logs. Credentials and response bodies are not printed.") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SystemExit("API connection failed; check TLS names, trusted CA, service health and network access.") from exc
