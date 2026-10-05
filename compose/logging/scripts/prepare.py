#!/usr/bin/env python3
"""Prepare local TLS and secret files inside the repository's Docker runner."""

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile


BASE = Path(__file__).resolve().parents[1]
SECRET_NAMES = (
    "elastic_password",
    "kibana_password",
    "logstash_password",
    "ingest_password",
    "logstash_keystore_password",
    "kibana_session_key",
    "kibana_saved_objects_key",
    "kibana_reporting_key",
)


def run(*args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def san(host):
    try:
        return f"IP:{ipaddress.ip_address(host)}"
    except ValueError:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", host):
            raise SystemExit(f"Invalid TLS host name: {host!r}")
        return f"DNS:{host}"


def secret_file(path, value, mode=0o640):
    path.write_text(value)
    path.chmod(mode)
    os.chown(path, 0, 0)


def generate(output, kibana_host, ingest_host):
    desired = {"format": 1, "kibana_host": kibana_host, "ingest_host": ingest_host}
    marker = output / "prepared.json"
    if output.exists():
        if not marker.is_file() or json.loads(marker.read_text()) != desired:
            raise SystemExit(
                "Existing runtime directory is incomplete or uses different hostnames. "
                "Refusing to replace keys; inspect it and perform an explicit rotation."
            )
        required = [output / "secrets" / name for name in SECRET_NAMES]
        required += [output / "ca-private" / name for name in ("ca.key", "ca.crt")]
        for service in ("elasticsearch", "kibana", "logstash"):
            required += [output / "tls" / service / name for name in ("server.key", "server.crt", "ca.crt")]
        required += [output / "tls" / "collector" / name for name in ("client.key", "client.crt", "ca.crt")]
        required.append(output / "tls" / "public" / "ca.crt")
        if not all(path.is_file() and path.stat().st_size for path in required) or not (output / "snapshots").is_dir():
            raise SystemExit("Existing initialization is missing files; refusing to regenerate credentials.")
        print("Existing certificates and secrets preserved; no keys or passwords replaced.")
        return

    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".logging-prepare-", dir=output.parent))
    try:
        for name in ("ca-private", "secrets", "tls", "tls/public", "snapshots"):
            (stage / name).mkdir(parents=True, exist_ok=True)
        for name in SECRET_NAMES:
            secret_file(stage / "secrets" / name, secrets.token_hex(32) + "\n")

        ca = stage / "ca-private"
        run("openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072", "-out", str(ca / "ca.key"))
        run(
            "openssl", "req", "-new", "-x509", "-sha256", "-days", "3650",
            "-key", str(ca / "ca.key"), "-out", str(ca / "ca.crt"),
            "-subj", "/CN=Platform Logging Private CA",
            "-addext", "basicConstraints=critical,CA:TRUE",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        )

        hosts = {
            "elasticsearch": ["elasticsearch", "localhost", "127.0.0.1"],
            "kibana": ["kibana", "localhost", "127.0.0.1", kibana_host],
            "logstash": ["logstash", "localhost", "127.0.0.1", ingest_host],
            "collector": ["platform-collector"],
        }
        for service, names in hosts.items():
            directory = stage / "tls" / service
            directory.mkdir()
            stem = "client" if service == "collector" else "server"
            key, certificate = directory / f"{stem}.key", directory / f"{stem}.crt"
            csr, extensions = stage / f"{service}.csr", stage / f"{service}.ext"
            # genpkey emits unencrypted PKCS#8 PEM, as required by Logstash inputs.
            run("openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072", "-out", str(key))
            run("openssl", "req", "-new", "-key", str(key), "-out", str(csr), "-subj", f"/CN={service}")
            usage = "clientAuth" if service == "collector" else "serverAuth"
            if service == "elasticsearch":
                usage += ",clientAuth"  # Transport TLS is mutual TLS.
            secret_file(
                extensions,
                "basicConstraints=critical,CA:FALSE\n"
                "keyUsage=critical,digitalSignature,keyEncipherment\n"
                f"extendedKeyUsage={usage}\n"
                f"subjectAltName={','.join(dict.fromkeys(san(name) for name in names))}\n",
            )
            run(
                "openssl", "x509", "-req", "-in", str(csr), "-CA", str(ca / "ca.crt"),
                "-CAkey", str(ca / "ca.key"), "-set_serial", str(secrets.randbits(128)),
                "-out", str(certificate), "-days", "365", "-sha256", "-extfile", str(extensions),
            )
            run("openssl", "verify", "-CAfile", str(ca / "ca.crt"), str(certificate))
            shutil.copyfile(ca / "ca.crt", directory / "ca.crt")
            csr.unlink()
            extensions.unlink()
        shutil.copyfile(ca / "ca.crt", stage / "tls" / "public" / "ca.crt")
        secret_file(stage / "prepared.json", json.dumps(desired, indent=2) + "\n")
        for path in [stage, *stage.rglob("*")]:
            path.chmod(0o750 if path.is_dir() else 0o640)
            os.chown(path, 0, 0)
        (ca / "ca.key").chmod(0o600)
        ca.chmod(0o700)
        os.chown(stage / "snapshots", 1000, 0)
        (stage / "snapshots").chmod(0o770)
        if output.exists():
            raise SystemExit("Runtime directory appeared during initialization; refusing to replace it.")
        stage.rename(output)
        print(f"Prepared {output}; no secret values were printed. Certificates expire in 365 days.")
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-root", required=True, help="Absolute checkout path on the Docker daemon host")
    parser.add_argument("--kibana-host", default="localhost")
    parser.add_argument("--ingest-host", default="localhost")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("Run with the repository Docker runner as root to prepare UID/GID permissions.")
    if not shutil.which("openssl"):
        raise SystemExit("OpenSSL is required inside the runner image.")
    host_root = Path(args.host_root)
    if not host_root.is_absolute() or any(c in str(host_root) for c in "\n\r'\"$"):
        raise SystemExit("--host-root must be an absolute path without quotes, dollar signs or newlines.")
    san(args.kibana_host)
    san(args.ingest_host)
    generate(BASE / "runtime", args.kibana_host, args.ingest_host)
    env_file = BASE / ".env"
    if env_file.exists():
        print("Existing .env preserved; verify its paths and KIBANA_PUBLIC_URL.")
    else:
        content = (BASE / ".env.example").read_text().replace("/absolute/path/to/k8s-dev-test", str(host_root))
        content = content.replace("https://localhost:5601", f"https://{args.kibana_host}:5601")
        secret_file(env_file, content, mode=0o600)
        print(f"Created {env_file}; review bind paths and public URL before deployment.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"OpenSSL failed with exit code {exc.returncode}; no existing initialization was replaced.") from exc
