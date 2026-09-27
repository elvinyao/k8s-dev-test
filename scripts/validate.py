#!/usr/bin/env python3
"""Check repository YAML without contacting a cluster or rendering Kustomize."""

from pathlib import Path
import re
import sys

import yaml
from yaml.constructor import ConstructorError


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = (
    "clusters",
    "bootstrap",
    "platform",
    "apps",
    "argocd-sys-settings",
    "argocd-app-settings",
    "compose",
)
KUSTOMIZATION_FILES = ("kustomization.yaml", "kustomization.yml", "Kustomization")


class UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader with duplicate mapping keys treated as errors."""

    def construct_mapping(self, node, deep=False):
        # Explicit duplicate keys are errors; overriding an inherited YAML
        # merge key is valid Compose syntax and must remain supported.
        seen = set()
        for key_node, value_node in node.value:
            if key_node.tag == 'tag:yaml.org,2002:merge':
                continue
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in seen
            except TypeError as exc:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "mapping keys must be hashable",
                    key_node.start_mark,
                ) from exc
            if duplicate:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"duplicate key: {key!r}",
                    key_node.start_mark,
                )
            seen.add(key)
        self.flatten_mapping(node)
        return super().construct_mapping(node, deep=deep)


def is_remote(resource):
    # Kustomize accepts URL-style and host/path-style Git references.
    return "://" in resource or resource.startswith("git@") or bool(
        re.match(r"^[\w-]+(?:\.[\w-]+)+/", resource)
    )


def main():
    errors = []
    remote_resources = set()
    document_count = 0
    local_resource_count = 0
    files = sorted(
        path
        for directory in SOURCE_DIRS
        for path in (ROOT / directory).rglob("*")
        if path.is_file()
        and not any(part in {'.local', 'secrets.local', 'data', 'backups', '.state'} for part in path.relative_to(ROOT).parts)
        and (
            path.name.endswith((".yaml", ".yml", ".yaml.example", ".yml.example"))
            or path.name == "Kustomization"
        )
    )
    if not files:
        errors.append("No source YAML files found.")

    for path in files:
        name = path.relative_to(ROOT).as_posix()
        try:
            documents = list(yaml.load_all(path.read_text(), Loader=UniqueKeyLoader))
        except (yaml.YAMLError, OSError, UnicodeError) as exc:
            errors.append(f"{name}: {exc}")
            continue

        for index, document in enumerate(documents, start=1):
            document_count += 1
            location = f"{name} (document {index})"
            if not isinstance(document, dict):
                if name.startswith('compose/') and isinstance(document, list):
                    continue
                errors.append(f"{location}: every YAML document must be a mapping.")
                continue

            if document.get("kind") == "Secret":
                for field in ("data", "stringData"):
                    if document.get(field):
                        errors.append(
                            f"{location}: Secret {field} must be empty; "
                            "keep credentials outside source control."
                        )

            if document.get("kind") != "Kustomization":
                continue
            resources = document.get("resources", [])
            if not isinstance(resources, list):
                errors.append(f"{location}: resources must be a list.")
                continue
            for resource in resources:
                if not isinstance(resource, str) or not resource.strip():
                    errors.append(f"{location}: each resource must be a nonempty string.")
                    continue
                if is_remote(resource):
                    remote_resources.add((name, resource))
                    if any(
                        token.lower() in {"latest", "stable"}
                        for token in re.split(r"[/?:&=#]", resource)
                    ):
                        errors.append(
                            f"{location}: floating remote resource is forbidden: {resource}"
                        )
                    continue

                local_resource_count += 1
                target = (path.parent / resource).resolve()
                if not target.is_relative_to(ROOT):
                    errors.append(f"{location}: resource escapes the repository: {resource}")
                elif target.is_file():
                    continue
                elif target.is_dir() and any(
                    (target / candidate).is_file() for candidate in KUSTOMIZATION_FILES
                ):
                    continue
                else:
                    errors.append(
                        f"{location}: resource must name an existing file or a directory "
                        f"containing a kustomization: {resource}"
                    )

    for name, resource in sorted(remote_resources):
        print(f"UNRENDERED remote resource: {name}: {resource}")
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    print(
        f"Static validation {'FAILED' if errors else 'PASSED'}: "
        f"{len(files)} files, {document_count} documents, "
        f"{local_resource_count} local resource references, "
        f"{len(remote_resources)} unrendered remote resources, {len(errors)} errors."
    )
    print(
        "Scope: YAML parsing, duplicate keys, local resource references, and Secret "
        "content only. No Kubernetes schema validation, Kustomize/Helm rendering, "
        "remote fetching, or cluster/runtime verification."
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
