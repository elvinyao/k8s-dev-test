"""Prepare local, ignored monitoring credentials; run only through .agent/run.sh."""

import argparse
import os
from pathlib import Path, PurePosixPath
import secrets


def write_once(path: Path, content: str, mode: int) -> None:
    """Never overwrite an existing password, encryption key or environment file."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(f"Preserved existing file: {path.relative_to(ROOT)}")
        return
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(content)
        stream.flush()
        os.fchmod(stream.fileno(), mode)
    print(f"Created file (content hidden): {path.relative_to(ROOT)}")


ROOT = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host-config-dir",
        required=True,
        help="Absolute path to this checkout's compose/monitoring/config on the Docker daemon host",
    )
    args = parser.parse_args()
    host_dir = args.host_config_dir
    if not PurePosixPath(host_dir).is_absolute() or any(c in host_dir for c in "\r\n'"):
        parser.error("--host-config-dir must be an absolute POSIX path without quotes/newlines")

    os.umask(0o077)
    secret_dir = ROOT / "config" / "secrets"
    secret_dir.mkdir(mode=0o700, exist_ok=True)
    secret_dir.chmod(0o700)
    (ROOT / "backups").mkdir(mode=0o700, exist_ok=True)
    # Compose file secrets are bind mounts; uid/gid/mode remapping is not
    # portable. The host directory is private, while each mounted file is
    # readable by Grafana's non-root UID. No secret is printed or put in .env.
    write_once(secret_dir / "grafana-admin-password", secrets.token_urlsafe(36) + "\n", 0o444)
    write_once(secret_dir / "grafana-secret-key", secrets.token_hex(32) + "\n", 0o444)
    environment = (ROOT / ".env.example").read_text(encoding="utf-8")
    environment = environment.replace(
        "MONITORING_CONFIG_DIR=/srv/platform/compose/monitoring/config",
        f"MONITORING_CONFIG_DIR='{host_dir}'",
    )
    write_once(ROOT / ".env", environment, 0o600)
    print("No services started. Confirm the daemon host path, review .env, and securely retain the encryption key.")


if __name__ == "__main__":
    main()
