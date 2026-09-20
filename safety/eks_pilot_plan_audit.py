"""Fail-closed structural audit of a proposed EKS creation plan (no AWS calls)."""

from __future__ import annotations

import ipaddress
import json
import sys
from collections import Counter
from typing import Any


class EksPilotPlanError(ValueError):
    """A Terraform plan does not match the disposable pilot topology."""


# Supporting resources from the pinned VPC/EKS modules. An addition to this
# inventory needs human review and an intentional code change, not auto-approval.
ALLOWED_TYPES = frozenset(
    {
        "aws_default_network_acl",
        "aws_default_route_table",
        "aws_default_security_group",
        "aws_ec2_tag",
        "aws_eip",
        "aws_eks_access_entry",
        "aws_eks_access_policy_association",
        "aws_eks_addon",
        "aws_eks_cluster",
        "aws_eks_node_group",
        "aws_iam_role",
        "aws_iam_role_policy_attachment",
        "aws_internet_gateway",
        "aws_nat_gateway",
        "aws_route",
        "aws_route_table",
        "aws_route_table_association",
        "aws_security_group",
        "aws_security_group_rule",
        "aws_subnet",
        "aws_vpc",
        "aws_vpc_security_group_egress_rule",
        "aws_vpc_security_group_ingress_rule",
        "null_resource",
        "terraform_data",
        "time_sleep",
    }
)

REQUIRED_COUNTS = {
    "aws_eip": 1,
    "aws_eks_addon": 3,
    "aws_eks_cluster": 1,
    "aws_eks_node_group": 1,
    "aws_internet_gateway": 1,
    "aws_nat_gateway": 1,
    "aws_subnet": 4,
    "aws_vpc": 1,
    "null_resource": 1,
    "terraform_data": 1,
}

EXPECTED_NULL_RESOURCE = (
    'module.eks[0].module.eks_managed_node_group["pilot"]'
    ".module.user_data.null_resource.validate_cluster_service_cidr"
)
EXPECTED_ADDON_ADDRESSES = {
    "vpc-cni": 'module.eks[0].aws_eks_addon.before_compute["vpc-cni"]',
    "kube-proxy": 'module.eks[0].aws_eks_addon.before_compute["kube-proxy"]',
    "coredns": 'module.eks[0].aws_eks_addon.this["coredns"]',
}

SUBNET_CIDRS = {"10.70.0.0/20", "10.70.16.0/20", "10.70.32.0/20", "10.70.48.0/20"}


def _only(changes: list[dict[str, Any]], resource_type: str) -> dict[str, Any]:
    return next(change for change in changes if change["type"] == resource_type)


def _has_managed_state(module: dict[str, Any]) -> bool:
    local_managed = any(
        item.get("mode", "managed") == "managed" for item in module.get("resources", [])
    )
    return local_managed or any(
        _has_managed_state(child) for child in module.get("child_modules", [])
    )


def audit_creation_plan(plan: dict[str, Any]) -> Counter[str]:
    """Validate a fresh, create-only plan and return exact managed-resource counts."""
    if not str(plan.get("format_version", "")).startswith("1."):
        raise EksPilotPlanError("expected Terraform JSON plan format 1.x")
    if plan.get("applyable") is not True or plan.get("complete") is not True or plan.get(
        "errored"
    ) is not False:
        raise EksPilotPlanError("plan must be complete, error-free, and applicable")
    if (plan.get("variables") or {}).get("enable_experiment", {}).get("value") is not True:
        raise EksPilotPlanError("plan must explicitly enable the disposable experiment")
    module_calls = ((plan.get("configuration") or {}).get("root_module") or {}).get(
        "module_calls"
    ) or {}
    vpc_dependencies = (module_calls.get("vpc") or {}).get("depends_on") or []
    eks_tag_references = (
        ((module_calls.get("eks") or {}).get("expressions") or {}).get("tags") or {}
    ).get("references") or []
    if (
        "terraform_data.approval_gate" not in vpc_dependencies
        or "terraform_data.approval_gate[0].id" not in eks_tag_references
    ):
        raise EksPilotPlanError("VPC and EKS module writes must depend on approval gate")
    if plan.get("resource_drift"):
        raise EksPilotPlanError("resource drift needs manual resolution before creation")
    prior = ((plan.get("prior_state") or {}).get("values") or {}).get("root_module") or {}
    if _has_managed_state(prior):
        raise EksPilotPlanError("creation plan must start from empty managed state")
    changes = plan.get("resource_changes")
    if not isinstance(changes, list) or not changes:
        raise EksPilotPlanError("plan has no resource_changes")

    managed = []
    for item in changes:
        if not isinstance(item, dict) or item.get("mode") not in {"managed", "data"}:
            raise EksPilotPlanError("plan contains an unrecognized resource change")
        if item["mode"] == "data":
            continue
        resource_type = item.get("type")
        address = item.get("address", "unknown")
        if resource_type not in ALLOWED_TYPES:
            raise EksPilotPlanError(f"unexpected managed resource type: {resource_type}")
        if resource_type == "null_resource" and address != EXPECTED_NULL_RESOURCE:
            raise EksPilotPlanError(f"unexpected validation null_resource: {address}")
        change = item.get("change", {})
        if change.get("actions") != ["create"] or change.get("before") is not None:
            raise EksPilotPlanError(f"not a fresh create-only plan: {address}")
        if item.get("previous_address") or change.get("importing"):
            raise EksPilotPlanError(f"moved/imported resource is not allowed: {address}")
        if item.get("deposed"):
            raise EksPilotPlanError(f"deposed resource is not allowed: {address}")
        if not isinstance(change.get("after"), dict):
            raise EksPilotPlanError(f"missing planned values: {address}")
        managed.append(item)

    counts = Counter(item["type"] for item in managed)
    for resource_type, expected in REQUIRED_COUNTS.items():
        if counts[resource_type] != expected:
            raise EksPilotPlanError(
                f"expected {expected} {resource_type}, found {counts[resource_type]}"
            )

    cluster = _only(managed, "aws_eks_cluster")["change"]["after"]
    if not str(cluster.get("name", "")).startswith("kubefit-eks-pilot"):
        raise EksPilotPlanError("cluster name is outside the disposable pilot prefix")
    if cluster.get("version") != "1.34":
        raise EksPilotPlanError("unexpected EKS Kubernetes version")
    vpc_config = cluster.get("vpc_config")
    if not isinstance(vpc_config, list) or len(vpc_config) != 1 or not isinstance(
        vpc_config[0], dict
    ):
        raise EksPilotPlanError("missing EKS VPC endpoint configuration")
    endpoint = vpc_config[0]
    cidrs = endpoint.get("public_access_cidrs")
    if endpoint.get("endpoint_private_access") is not True or endpoint.get(
        "endpoint_public_access"
    ) is not True:
        raise EksPilotPlanError("EKS endpoint access differs from the reviewed topology")
    try:
        if len(cidrs) != 1 or ipaddress.ip_network(cidrs[0], strict=True).prefixlen != 32:
            raise ValueError("not a single /32")
        if not isinstance(ipaddress.ip_network(cidrs[0]), ipaddress.IPv4Network):
            raise ValueError("not IPv4")
    except (TypeError, ValueError):
        raise EksPilotPlanError("EKS public endpoint must allow exactly one IPv4 /32") from None

    node_group = _only(managed, "aws_eks_node_group")["change"]["after"]
    scaling = node_group.get("scaling_config")
    if not isinstance(scaling, list) or len(scaling) != 1 or not isinstance(scaling[0], dict):
        raise EksPilotPlanError("missing managed node-group scaling configuration")
    if (
        node_group.get("instance_types") != ["m6i.large"]
        or node_group.get("capacity_type") != "ON_DEMAND"
        or node_group.get("disk_size") != 20
        or any(scaling[0].get(key) != 2 for key in ("min_size", "max_size", "desired_size"))
    ):
        raise EksPilotPlanError("managed node group differs from two on-demand m6i.large nodes")

    if _only(managed, "aws_vpc")["change"]["after"].get("cidr_block") != "10.70.0.0/16":
        raise EksPilotPlanError("unexpected VPC CIDR")
    actual_subnets = {
        item["change"]["after"].get("cidr_block")
        for item in managed
        if item["type"] == "aws_subnet"
    }
    if actual_subnets != SUBNET_CIDRS:
        raise EksPilotPlanError("subnet CIDRs differ from the reviewed topology")
    addon_items = [item for item in managed if item["type"] == "aws_eks_addon"]
    for item in addon_items:
        addon = item["change"]["after"]
        name = addon.get("addon_name")
        if (
            item["address"] != EXPECTED_ADDON_ADDRESSES.get(name)
            or addon.get("preserve") is not False
        ):
            raise EksPilotPlanError("EKS managed add-ons differ from the required set")
    return counts


def main() -> int:
    try:
        raw = sys.stdin.read(10_000_001)
        if len(raw) > 10_000_000:
            raise EksPilotPlanError("plan JSON exceeds the 10 MB review limit")
        plan = json.loads(raw)
        if not isinstance(plan, dict):
            raise EksPilotPlanError("plan JSON must be an object")
        counts = audit_creation_plan(plan)
    except (json.JSONDecodeError, EksPilotPlanError) as exc:
        print(f"EKS creation-plan audit: FAIL — {exc}", file=sys.stderr)
        return 1

    print("EKS creation-plan audit: PASS — reviewed topology only")
    for resource_type, count in sorted(counts.items()):
        print(f"  {resource_type}: {count}")
    print("This is not cost approval or permission to apply. Review every plan entry manually.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
