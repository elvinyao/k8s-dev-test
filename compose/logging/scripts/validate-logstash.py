#!/usr/bin/env python3
"""Validate the exact Logstash entrypoint/pipeline in an isolated Compose job."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "compose/logging"


def main():
    os.umask(0o077)
    host_root = Path(os.environ.get("PLATFORM_HOST_ROOT", ""))
    if not host_root.is_absolute() or str(host_root) == "/workspace":
        raise SystemExit("Run through bash .agent/run.sh --docker --toolbox to supply the host path.")
    identity = uuid.uuid4().hex[:12]
    work = ROOT / ".local" / ("logging-config-check-" + identity)
    fixture = work / "fixture"
    compose_directory = fixture / "compose/logging"
    report = {"startedAt": datetime.now(timezone.utc).isoformat(), "status": "running",
              "project": "elk-config-check-" + identity,
              "scope": "Logstash keystore entrypoint and pipeline configuration only; no Elasticsearch or Kibana started"}
    work.mkdir(parents=True, mode=0o700)
    log_path = work / "logstash.log"
    compose = ["docker", "compose", "-p", "elk-config-check-" + identity,
               "--project-directory", str(compose_directory),
               "--env-file", str(compose_directory / ".env"),
               "-f", str(compose_directory / "compose.yaml")]
    credentials = []
    prepared = False
    cleaned = True
    try:
        shutil.copytree(SOURCE, compose_directory,
                        ignore=shutil.ignore_patterns("runtime", ".env", ".env.*", "__pycache__"))
        shutil.copyfile(SOURCE / ".env.example", compose_directory / ".env.example")
        subprocess.run([sys.executable, str(compose_directory / "scripts/prepare.py"),
                        "--host-root", str(host_root / fixture.relative_to(ROOT))],
                       check=True, stdout=subprocess.DEVNULL)
        prepared = True
        credentials = [path.read_text().strip() for path in (compose_directory / "runtime/secrets").iterdir()]
        inputs = [SOURCE / "compose.yaml", SOURCE / ".env.example",
                  *sorted((SOURCE / "config").rglob("*")), *sorted((SOURCE / "scripts").glob("*"))]
        report["sourceFiles"] = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in inputs if path.is_file()}
        configuration = json.loads(subprocess.check_output([*compose, "config", "--format", "json"]))
        image = configuration["services"]["logstash"]["image"]
        report["image"] = image
        report["command"] = ["--config.test_and_exit", "--log.level", "warn"]
        with log_path.open("w") as output:
            result = subprocess.run([*compose, "run", "--rm", "--no-deps", "logstash",
                                     "--config.test_and_exit", "--log.level", "warn"],
                                    stdout=output, stderr=subprocess.STDOUT, timeout=900)
        report["exitCode"] = result.returncode
        if result.returncode:
            raise RuntimeError("Logstash validation failed; inspect the redacted log.")
        report["imageDigests"] = json.loads(subprocess.check_output(
            ["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"]
        ))
        report["status"] = "passed"
    except (OSError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        report["status"] = "failed"
        report["error"] = str(exc) or "Interrupted"
    finally:
        # The UUID project was created only by this invocation. Never clean up
        # platform-logging or another caller's containers/volumes.
        if prepared:
            try:
                with log_path.open("a") as output:
                    cleanup = subprocess.run([*compose, "down", "--volumes", "--remove-orphans"],
                                             stdout=output, stderr=subprocess.STDOUT, timeout=120)
                report["cleanupExitCode"] = cleanup.returncode
                cleaned = cleanup.returncode == 0
            except (OSError, subprocess.SubprocessError) as exc:
                cleaned = False
                report["cleanupError"] = str(exc)
            if not cleaned:
                report["status"] = "failed"
                report["error"] = "Own-project cleanup failed; fixture retained for cleanup using the reported project name."
        if log_path.exists():
            content = log_path.read_text(errors="replace")
            for secret in credentials:
                if secret:
                    content = content.replace(secret, "[REDACTED]")
            log_path.write_text(content)
        if fixture.exists() and cleaned:
            shutil.rmtree(fixture)
        report["finishedAt"] = datetime.now(timezone.utc).isoformat()
        (work / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f'{report["status"].upper()}: {work / "report.json"}')
    print(f"Log: {log_path}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
