"""Install pinned upstream Linux tools inside the toolbox image only."""

import hashlib
import io
from pathlib import Path
import platform
import tarfile
import urllib.request


def download(url):
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def verified(url, checksum_url):
    payload = download(url)
    expected = download(checksum_url).decode().split()[0].lower()
    actual = hashlib.sha256(payload).hexdigest()
    if len(expected) != 64 or actual != expected:
        raise ValueError(f"SHA256 mismatch: {url}")
    return payload


def install(name, payload):
    target = Path('/usr/local/bin') / name
    target.write_bytes(payload)
    target.chmod(0o755)
    print(f"Installed {name}")


arch = {'x86_64': 'amd64', 'aarch64': 'arm64'}[platform.machine()]
kubectl = f'https://dl.k8s.io/release/v1.35.8/bin/linux/{arch}/kubectl'
install('kubectl', verified(kubectl, kubectl + '.sha256'))
kind = f'https://github.com/kubernetes-sigs/kind/releases/download/v0.33.0/kind-linux-{arch}'
install('kind', verified(kind, kind + '.sha256sum'))
helm = f'https://get.helm.sh/helm-v3.22.0-linux-{arch}.tar.gz'
with tarfile.open(fileobj=io.BytesIO(verified(helm, helm + '.sha256sum'))) as archive:
    install('helm', archive.extractfile(f'linux-{arch}/helm').read())
