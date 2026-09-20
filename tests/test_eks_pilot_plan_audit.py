from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy

import pytest

from safety.eks_pilot_plan_audit import (
    EXPECTED_ADDON_ADDRESSES,
    EXPECTED_NULL_RESOURCE,
    EksPilotPlanError,
    audit_creation_plan,
)


def _change(resource_type: str, after: dict, index: int = 0) -> dict:
    return {
        "address": f"module.pilot.{resource_type}.this[{index}]",
        "mode": "managed",
        "type": resource_type,
        "change": {"actions": ["create"], "before": None, "after": after},
    }


def _plan() -> dict:
    resources = [
        _change("aws_vpc", {"cidr_block": "10.70.0.0/16"}),
        _change("aws_internet_gateway", {}),
        _change("aws_eip", {}),
        _change("aws_nat_gateway", {}),
        _change("terraform_data", {}),
        _change("null_resource", {}),
        _change("aws_eks_addon", {"addon_name": "vpc-cni", "preserve": False}, 0),
        _change("aws_eks_addon", {"addon_name": "kube-proxy", "preserve": False}, 1),
        _change("aws_eks_addon", {"addon_name": "coredns", "preserve": False}, 2),
        _change(
            "aws_eks_cluster",
            {
                "name": "kubefit-eks-pilot",
                "version": "1.34",
                "vpc_config": [
                    {
                        "endpoint_private_access": True,
                        "endpoint_public_access": True,
                        "public_access_cidrs": ["203.0.113.7/32"],
                    }
                ],
            },
        ),
        _change(
            "aws_eks_node_group",
            {
                "instance_types": ["m6i.large"],
                "capacity_type": "ON_DEMAND",
                "disk_size": 20,
                "scaling_config": [{"min_size": 2, "max_size": 2, "desired_size": 2}],
            },
        ),
    ]
    for index, cidr in enumerate(
        ("10.70.0.0/20", "10.70.16.0/20", "10.70.32.0/20", "10.70.48.0/20")
    ):
        resources.append(_change("aws_subnet", {"cidr_block": cidr}, index))
    resources[5]["address"] = EXPECTED_NULL_RESOURCE
    for item in resources:
        if item["type"] == "aws_eks_addon":
            item["address"] = EXPECTED_ADDON_ADDRESSES[item["change"]["after"]["addon_name"]]
    return {
        "format_version": "1.2",
        "applyable": True,
        "complete": True,
        "errored": False,
        "prior_state": None,
        "variables": {"enable_experiment": {"value": True}},
        "configuration": {
            "root_module": {
                "module_calls": {
                    "vpc": {"depends_on": ["terraform_data.approval_gate"]},
                    "eks": {
                        "expressions": {
                            "tags": {"references": ["terraform_data.approval_gate[0].id"]}
                        }
                    },
                }
            }
        },
        "resource_changes": resources,
    }


def test_accepts_expected_create_only_topology() -> None:
    counts = audit_creation_plan(_plan())
    assert counts["aws_eks_cluster"] == 1
    assert counts["aws_subnet"] == 4


def test_cli_accepts_plan_json_from_standard_input() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "safety.eks_pilot_plan_audit"],
        input=json.dumps(_plan()),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert "aws_nat_gateway: 1" in result.stdout


@pytest.mark.parametrize("resource_type", ["aws_lb", "aws_cloudwatch_log_group", "aws_kms_key"])
def test_rejects_unreviewed_chargeable_resource(resource_type: str) -> None:
    plan = _plan()
    plan["resource_changes"].append(_change(resource_type, {}))
    with pytest.raises(EksPilotPlanError, match="unexpected managed resource"):
        audit_creation_plan(plan)


def test_rejects_second_nat_gateway() -> None:
    plan = _plan()
    plan["resource_changes"].append(_change("aws_nat_gateway", {}, 1))
    with pytest.raises(EksPilotPlanError, match="expected 1 aws_nat_gateway"):
        audit_creation_plan(plan)


def test_rejects_missing_required_addon() -> None:
    plan = _plan()
    plan["resource_changes"] = [
        item
        for item in plan["resource_changes"]
        if item["change"]["after"].get("addon_name") != "vpc-cni"
    ]
    with pytest.raises(EksPilotPlanError, match="expected 3 aws_eks_addon"):
        audit_creation_plan(plan)


def test_rejects_wrong_addon() -> None:
    plan = _plan()
    plan["resource_changes"][6]["change"]["after"]["addon_name"] = "aws-ebs-csi-driver"
    with pytest.raises(EksPilotPlanError, match="required set"):
        audit_creation_plan(plan)


def test_rejects_cni_after_compute() -> None:
    plan = _plan()
    plan["resource_changes"][6]["address"] = 'module.eks[0].aws_eks_addon.this["vpc-cni"]'
    with pytest.raises(EksPilotPlanError, match="required set"):
        audit_creation_plan(plan)


def test_rejects_addon_preservation() -> None:
    plan = _plan()
    plan["resource_changes"][6]["change"]["after"]["preserve"] = True
    with pytest.raises(EksPilotPlanError, match="required set"):
        audit_creation_plan(plan)


def test_rejects_another_null_resource() -> None:
    plan = _plan()
    plan["resource_changes"].append(_change("null_resource", {}))
    with pytest.raises(EksPilotPlanError, match="unexpected validation null_resource"):
        audit_creation_plan(plan)


@pytest.mark.parametrize("actions", [["delete"], ["delete", "create"], ["update"]])
def test_rejects_non_create_action(actions: list[str]) -> None:
    plan = _plan()
    plan["resource_changes"][0]["change"]["actions"] = actions
    with pytest.raises(EksPilotPlanError, match="create-only"):
        audit_creation_plan(plan)


@pytest.mark.parametrize("cidr", ["0.0.0.0/0", "203.0.113.0/24", "::1/128"])
def test_rejects_broad_or_non_ipv4_endpoint(cidr: str) -> None:
    plan = _plan()
    plan["resource_changes"][9]["change"]["after"]["vpc_config"][0][
        "public_access_cidrs"
    ] = [cidr]
    with pytest.raises(EksPilotPlanError, match="IPv4 /32"):
        audit_creation_plan(plan)


def test_rejects_changed_node_count() -> None:
    plan = _plan()
    plan["resource_changes"][10]["change"]["after"]["scaling_config"][0][
        "desired_size"
    ] = 3
    with pytest.raises(EksPilotPlanError, match="two on-demand"):
        audit_creation_plan(plan)


def test_rejects_preexisting_managed_state() -> None:
    plan = _plan()
    plan["prior_state"] = {"values": {"root_module": {"resources": [{"mode": "managed"}]}}}
    with pytest.raises(EksPilotPlanError, match="empty managed state"):
        audit_creation_plan(plan)


def test_rejects_disabled_experiment() -> None:
    plan = deepcopy(_plan())
    plan["variables"]["enable_experiment"]["value"] = False
    with pytest.raises(EksPilotPlanError, match="explicitly enable"):
        audit_creation_plan(plan)


def test_rejects_missing_eks_approval_dependency() -> None:
    plan = _plan()
    calls = plan["configuration"]["root_module"]["module_calls"]
    calls["eks"]["expressions"]["tags"]["references"] = []
    with pytest.raises(EksPilotPlanError, match="approval gate"):
        audit_creation_plan(plan)


def test_rejects_missing_vpc_approval_dependency() -> None:
    plan = _plan()
    plan["configuration"]["root_module"]["module_calls"]["vpc"]["depends_on"] = []
    with pytest.raises(EksPilotPlanError, match="approval gate"):
        audit_creation_plan(plan)


@pytest.mark.parametrize(
    "field,value", [("applyable", False), ("complete", False), ("errored", True)]
)
def test_rejects_incomplete_or_errored_plan(field: str, value: bool) -> None:
    plan = _plan()
    plan[field] = value
    with pytest.raises(EksPilotPlanError, match="complete, error-free"):
        audit_creation_plan(plan)
