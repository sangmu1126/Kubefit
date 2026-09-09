from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode
from yaml.tokens import AliasToken, AnchorToken

MAX_MANIFEST_BYTES = 1_000_000


class _UniqueKeySafeLoader(yaml.SafeLoader):
    def construct_mapping(
        self,
        node: MappingNode,
        deep: bool = False,
    ) -> dict[object, object]:
        mapping: dict[object, object] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "mapping keys must be scalar values",
                    key_node.start_mark,
                ) from exc
            if duplicate:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"duplicate mapping key: {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


class DeploymentChangeError(RuntimeError):
    """Raised when a Deployment change cannot be inspected unambiguously."""


class FieldChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    before: object | None
    after: object | None


class ChangeLocation(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str


class DeploymentChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    namespace: str = Field(min_length=1)
    deployment: str = Field(min_length=1)
    status: Literal["supported", "unsupported", "unchanged"]
    supported_changes: list[FieldChange]
    unsupported_changes: list[ChangeLocation]


def inspect_deployment_change(
    base_path: Path,
    candidate_path: Path,
    *,
    namespace: str,
    deployment: str,
) -> DeploymentChange:
    """Compare one apps/v1 Deployment without treating formatting as a change."""
    base = _load_target(base_path, namespace, deployment)
    candidate = _load_target(candidate_path, namespace, deployment)
    changes = _diff(base, candidate)
    supported = [change for change in changes if _is_supported(change.path)]
    unsupported = [
        ChangeLocation(path=change.path)
        for change in changes
        if not _is_supported(change.path)
    ]
    status: Literal["supported", "unsupported", "unchanged"]
    if unsupported:
        status = "unsupported"
    elif supported:
        status = "supported"
    else:
        status = "unchanged"
    return DeploymentChange(
        namespace=namespace,
        deployment=deployment,
        status=status,
        supported_changes=supported,
        unsupported_changes=unsupported,
    )


def _load_target(path: Path, namespace: str, deployment: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise DeploymentChangeError("manifest path must be a regular, non-symlinked file")
    try:
        content = path.read_bytes()
        if len(content) > MAX_MANIFEST_BYTES:
            raise DeploymentChangeError("manifest exceeds the 1 MB inspection limit")
        text = content.decode("utf-8")
        if any(isinstance(token, AnchorToken | AliasToken) for token in yaml.scan(text)):
            raise DeploymentChangeError("manifest anchors and aliases are not supported")
        documents = list(yaml.load_all(text, Loader=_UniqueKeySafeLoader))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise DeploymentChangeError(f"could not parse manifest: {path}") from exc
    matches = []
    for document in documents:
        if not isinstance(document, dict):
            continue
        metadata = document.get("metadata")
        if not isinstance(metadata, dict):
            continue
        actual_namespace = metadata.get("namespace", "default")
        if (
            document.get("apiVersion") == "apps/v1"
            and document.get("kind") == "Deployment"
            and metadata.get("name") == deployment
            and actual_namespace == namespace
        ):
            matches.append(document)
    if len(matches) != 1:
        raise DeploymentChangeError(
            f"expected exactly one apps/v1 Deployment {namespace}/{deployment} in {path}"
        )
    _validate_container_identity(matches[0], path)
    return matches[0]


def _validate_container_identity(document: dict[str, Any], path: Path) -> None:
    try:
        containers = document["spec"]["template"]["spec"]["containers"]
    except (KeyError, TypeError) as exc:
        raise DeploymentChangeError(f"Deployment has no container list: {path}") from exc
    if not isinstance(containers, list) or not containers:
        raise DeploymentChangeError(f"Deployment has no container list: {path}")
    names = [item.get("name") for item in containers if isinstance(item, dict)]
    if len(names) != len(containers) or any(not isinstance(name, str) for name in names):
        raise DeploymentChangeError(f"Deployment contains an unnamed container: {path}")
    if len(set(names)) != len(names):
        raise DeploymentChangeError(f"Deployment contains duplicate container names: {path}")


def _diff(before: object, after: object, path: str = "") -> list[FieldChange]:
    if isinstance(before, dict) and isinstance(after, dict):
        changes = []
        for key in sorted(set(before) | set(after)):
            child = f"{path}/{_escape_pointer(str(key))}"
            if key not in before:
                changes.append(FieldChange(path=child, before=None, after=after[key]))
            elif key not in after:
                changes.append(FieldChange(path=child, before=before[key], after=None))
            else:
                changes.extend(_diff(before[key], after[key], child))
        return changes
    if isinstance(before, list) and isinstance(after, list):
        changes = []
        for index in range(max(len(before), len(after))):
            child = f"{path}/{index}"
            if index >= len(before):
                changes.append(FieldChange(path=child, before=None, after=after[index]))
            elif index >= len(after):
                changes.append(FieldChange(path=child, before=before[index], after=None))
            else:
                changes.extend(_diff(before[index], after[index], child))
        return changes
    if before != after:
        return [FieldChange(path=path or "/", before=before, after=after)]
    return []


def _is_supported(path: str) -> bool:
    if path == "/spec/replicas":
        return True
    parts = path.split("/")
    container_prefix = ["", "spec", "template", "spec", "containers"]
    if parts[:5] != container_prefix or len(parts) < 7 or not parts[5].isdigit():
        return False
    if parts[6:] == ["image"]:
        return True
    return parts[6:] in (
        ["resources", "requests", "cpu"],
        ["resources", "requests", "memory"],
        ["resources", "limits", "cpu"],
        ["resources", "limits", "memory"],
    )


def _escape_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")
