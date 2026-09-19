import json
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation

from recommender import CurrentResources


class KubernetesCollectionError(RuntimeError):
    """Raised when workload metadata cannot be collected through kubectl."""


@dataclass(frozen=True)
class DeploymentResources:
    namespace: str
    name: str
    uid: str
    created_at: datetime
    container: str
    pods: list[str]
    replica_sets: list[str]
    desired_replicas: int
    available_replicas: int
    container_status_count: int
    restart_count: int
    oom_killed_count: int
    pod_runtime_statuses: tuple["PodContainerRuntimeStatus", ...]
    resources: CurrentResources
    pod_uids: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PodContainerRuntimeStatus:
    pod: str
    restart_count: int
    oom_killed: bool


Runner = Callable[[Sequence[str]], str]

_QUANTITY_PATTERN = re.compile(
    r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+))"
    r"((?:[eE][+-]?\d+)|(?:Ki|Mi|Gi|Ti|Pi|Ei)|(?:n|u|m|k|K|M|G|T|P|E)?)$"
)
_DECIMAL_SI = {
    "": Decimal(1),
    "n": Decimal("1e-9"),
    "u": Decimal("1e-6"),
    "m": Decimal("1e-3"),
    "k": Decimal("1e3"),
    "M": Decimal("1e6"),
    "G": Decimal("1e9"),
    "T": Decimal("1e12"),
    "P": Decimal("1e15"),
    "E": Decimal("1e18"),
}
_BINARY_SI = {
    "Ki": Decimal(2**10),
    "Mi": Decimal(2**20),
    "Gi": Decimal(2**30),
    "Ti": Decimal(2**40),
    "Pi": Decimal(2**50),
    "Ei": Decimal(2**60),
}


def _run(command: Sequence[str]) -> str:
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise KubernetesCollectionError(f"kubectl failed: {detail.strip()}") from exc
    return result.stdout


def _parse_quantity(value: str) -> Decimal:
    match = _QUANTITY_PATTERN.fullmatch(value)
    if match is None:
        raise KubernetesCollectionError(f"invalid Kubernetes quantity: {value!r}")

    number, suffix = match.groups()
    try:
        if suffix in _DECIMAL_SI:
            multiplier = _DECIMAL_SI[suffix]
        elif suffix in _BINARY_SI:
            multiplier = _BINARY_SI[suffix]
        else:
            multiplier = Decimal(10) ** int(suffix[1:])
        return Decimal(number) * multiplier
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise KubernetesCollectionError(
            f"invalid Kubernetes quantity: {value!r}"
        ) from exc


def _ceil_to_int(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def parse_cpu_millicores(value: str) -> int:
    return _ceil_to_int(_parse_quantity(value) * 1000)


def parse_memory_mib(value: str) -> int:
    return _ceil_to_int(_parse_quantity(value) / Decimal(2**20))


_cpu_millicores = parse_cpu_millicores
_memory_mib = parse_memory_mib


def compile_label_selector(selector: dict[str, object]) -> str:
    match_labels = selector.get("matchLabels", {})
    if not isinstance(match_labels, dict):
        raise KubernetesCollectionError("selector matchLabels must be an object")
    requirements = [
        f"{key}={value}"
        for key, value in sorted(match_labels.items())
    ]
    expressions = selector.get("matchExpressions", [])
    if not isinstance(expressions, list) or not all(
        isinstance(expression, dict) for expression in expressions
    ):
        raise KubernetesCollectionError("selector matchExpressions must be a list of objects")

    for expression in sorted(
        expressions,
        key=lambda item: (
            str(item.get("key", "")),
            str(item.get("operator", "")),
            tuple(str(value) for value in item.get("values", [])),
        ),
    ):
        key = expression.get("key")
        operator = expression.get("operator")
        values = expression.get("values", [])
        if not isinstance(key, str) or not key or not isinstance(values, list):
            raise KubernetesCollectionError("selector expression has an invalid key or values")
        if operator in {"In", "NotIn"}:
            if not values or not all(isinstance(value, str) for value in values):
                raise KubernetesCollectionError(
                    f"selector operator {operator} requires string values"
                )
            keyword = "in" if operator == "In" else "notin"
            requirements.append(f"{key} {keyword} ({','.join(sorted(values))})")
        elif operator in {"Exists", "DoesNotExist"}:
            if values:
                raise KubernetesCollectionError(
                    f"selector operator {operator} does not accept values"
                )
            requirements.append(key if operator == "Exists" else f"!{key}")
        else:
            raise KubernetesCollectionError(f"unsupported selector operator: {operator!r}")

    if not requirements:
        raise KubernetesCollectionError("deployment selector must not be empty")
    return ",".join(requirements)


class KubectlDeploymentCollector:
    def __init__(self, runner: Runner = _run, context: str | None = None) -> None:
        self._runner = runner
        self._context = context

    def _command(self, *args: str) -> list[str]:
        command = ["kubectl"]
        if self._context:
            command.extend(["--context", self._context])
        command.extend(args)
        return command

    def collect(
        self, namespace: str, deployment: str, container_name: str | None = None
    ) -> DeploymentResources:
        raw = self._runner(
            self._command("get", "deployment", deployment, "-n", namespace, "-o", "json")
        )
        document = json.loads(raw)
        metadata = document.get("metadata", {})
        uid = metadata.get("uid")
        created_at_value = metadata.get("creationTimestamp")
        if not uid or not created_at_value:
            raise KubernetesCollectionError(
                "deployment must include metadata.uid and metadata.creationTimestamp"
            )
        try:
            created_at = datetime.fromisoformat(created_at_value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise KubernetesCollectionError(
                f"deployment has an invalid creation timestamp: {created_at_value!r}"
            ) from exc
        containers = document["spec"]["template"]["spec"]["containers"]
        if container_name is None and len(containers) != 1:
            names = ", ".join(item["name"] for item in containers)
            raise KubernetesCollectionError(f"select a container from: {names}")
        container = next(
            (item for item in containers if item["name"] == container_name),
            containers[0] if container_name is None else None,
        )
        if container is None:
            raise KubernetesCollectionError(f"container {container_name!r} was not found")
        resources = container.get("resources", {})
        requests = resources.get("requests", {})
        limits = resources.get("limits", {})
        required = [
            requests.get("cpu"),
            limits.get("cpu"),
            requests.get("memory"),
            limits.get("memory"),
        ]
        if any(value is None for value in required):
            raise KubernetesCollectionError(
                "container must define CPU and memory requests and limits"
            )

        label_selector = compile_label_selector(document["spec"]["selector"])
        replica_sets_raw = self._runner(
            self._command(
                "get", "replicasets", "-n", namespace, "-l", label_selector, "-o", "json"
            )
        )
        replica_sets = [
            item["metadata"]["name"]
            for item in json.loads(replica_sets_raw)["items"]
            if any(
                owner.get("controller") is True
                and owner.get("kind") == "Deployment"
                and owner.get("uid") == uid
                for owner in item["metadata"].get("ownerReferences", [])
            )
        ]
        if not replica_sets:
            raise KubernetesCollectionError(
                "deployment has no ReplicaSets owned by its current UID"
            )

        pods_raw = self._runner(
            self._command("get", "pods", "-n", namespace, "-l", label_selector, "-o", "json")
        )
        replica_set_names = set(replica_sets)
        pod_items = [
            item
            for item in json.loads(pods_raw)["items"]
            if any(
                owner.get("controller") is True
                and owner.get("kind") == "ReplicaSet"
                and owner.get("name") in replica_set_names
                for owner in item["metadata"].get("ownerReferences", [])
            )
        ]
        pods = [item["metadata"]["name"] for item in pod_items]
        if not pods:
            raise KubernetesCollectionError(
                "deployment has no Pods owned by its current ReplicaSets"
            )

        named_container_statuses = [
            (item["metadata"]["name"], status)
            for item in pod_items
            for status in item.get("status", {}).get("containerStatuses", [])
            if status.get("name") == container["name"]
        ]
        pod_runtime_statuses = tuple(
            PodContainerRuntimeStatus(
                pod=pod,
                restart_count=status.get("restartCount", 0),
                oom_killed=_was_oom_killed(status),
            )
            for pod, status in named_container_statuses
        )
        container_statuses = [status for _, status in named_container_statuses]
        restart_count = sum(status.get("restartCount", 0) for status in container_statuses)
        oom_killed_count = sum(
            _was_oom_killed(status) for status in container_statuses
        )
        return DeploymentResources(
            namespace=namespace,
            name=deployment,
            uid=uid,
            created_at=created_at,
            container=container["name"],
            pods=pods,
            replica_sets=sorted(replica_sets),
            desired_replicas=document["spec"].get("replicas", 1),
            available_replicas=document.get("status", {}).get("availableReplicas", 0),
            container_status_count=len(container_statuses),
            restart_count=restart_count,
            oom_killed_count=oom_killed_count,
            pod_runtime_statuses=pod_runtime_statuses,
            resources=CurrentResources(
                cpu_request_millicores=parse_cpu_millicores(requests["cpu"]),
                cpu_limit_millicores=parse_cpu_millicores(limits["cpu"]),
                memory_request_mib=parse_memory_mib(requests["memory"]),
                memory_limit_mib=parse_memory_mib(limits["memory"]),
            ),
            pod_uids={
                item["metadata"]["name"]: item["metadata"].get("uid", "")
                for item in pod_items
            },
        )


def _was_oom_killed(status: dict[str, object]) -> bool:
    for state_name in ("state", "lastState"):
        state = status.get(state_name, {})
        if isinstance(state, dict):
            terminated = state.get("terminated", {})
            if isinstance(terminated, dict) and terminated.get("reason") == "OOMKilled":
                return True
    return False
