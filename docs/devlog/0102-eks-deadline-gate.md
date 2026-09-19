# 0102 — EKS deadline gate

## Why

The EKS approval gate previously accepted any parseable `stop_at_utc`. A date
in the past or weeks away could pass the format check, leaving the stated
four-hour cost experiment unenforced even before planning.

## How and what

```text
plan time ── stop_at_utc must be > now and <= now + 4h
    │
    └─ saved plan may age ── apply start: stop_at_utc must still be > now
                              │
                              └─ human cleanup by deadline (not automated)
```

Added UTC-format, future, and four-hour-window preconditions to the existing
`terraform_data.approval_gate`. The plan-time check uses Terraform's
`plantimestamp()`; the apply-time stale-plan check uses `timestamp()`. The
VPC and EKS modules already depend on this gate. HashiCorp documents that
`plantimestamp()` is available during planning while `timestamp()` is unknown
until apply, so these checks cover different moments without implying that a
deadline timer is running. References:
[plantimestamp](https://developer.hashicorp.com/terraform/language/functions/plantimestamp),
[timestamp evaluation](https://developer.hashicorp.com/terraform/language/expressions/function-calls),
[preconditions](https://developer.hashicorp.com/terraform/language/validate).

## Verification and limits

- `terraform fmt -check`: passed.
- `terraform validate -no-color`: passed.
- Default `terraform plan -detailed-exitcode -input=false -refresh=false
  -lock=false -no-color`: exit 0, **No changes**.
- No enabled plan or apply was run; the live AWS branch and actual cleanup
  timer remain untested. A passing apply-time check only establishes that the
  deadline was in the future when apply began. It neither cancels a slow EKS
  creation nor removes resources.

## Next question

How can the reviewed, enabled Terraform plan be compared with a strict
resource and cost inventory before any AWS write, without treating a plan as
authorization to apply?
