terraform {
  required_version = ">= 1.9.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = "ap-northeast-2"

  default_tags {
    tags = {
      Project = "KubeFit"
      Purpose = "temporary-eks-compatibility-pilot"
      Owner   = var.owner_tag
    }
  }
}

variable "enable_experiment" {
  description = "False by default: a normal plan must create nothing. Set true only after the GO/NO-GO review."
  type        = bool
  default     = false
}

variable "cluster_name" {
  description = "Unique, disposable cluster name. Check for collisions before enabling."
  type        = string
  default     = "kubefit-eks-pilot"
}

variable "owner_tag" {
  description = "Owner contact or handle for experiment resources."
  type        = string
  default     = "unapproved"
}

variable "approved_account_id" {
  description = "Exact AWS account ID approved for this experiment."
  type        = string
  default     = ""
}

variable "operator_cidr" {
  description = "The operator's current public IPv4 address as a /32 CIDR; never 0.0.0.0/0."
  type        = string
  default     = ""
}

variable "approved_budget_usd" {
  description = "Recorded human-approved ceiling. This is not an AWS spending cap."
  type        = number
  default     = 0
}

variable "stop_at_utc" {
  description = "Human cleanup deadline in RFC3339 UTC, at most four hours after planning. No automatic deletion is implied."
  type        = string
  default     = ""
}

data "aws_caller_identity" "current" {
  count = var.enable_experiment ? 1 : 0
}

data "aws_availability_zones" "available" {
  count = var.enable_experiment ? 1 : 0
  state = "available"
}

resource "terraform_data" "approval_gate" {
  count = var.enable_experiment ? 1 : 0

  lifecycle {
    precondition {
      condition     = var.approved_account_id == data.aws_caller_identity.current[0].account_id
      error_message = "approved_account_id must match the active AWS account."
    }
    precondition {
      condition     = can(regex("^[0-9]{1,3}(\\.[0-9]{1,3}){3}/32$", var.operator_cidr)) && can(cidrhost(var.operator_cidr, 0))
      error_message = "operator_cidr must be one valid IPv4 /32 address."
    }
    precondition {
      condition     = var.approved_budget_usd > 0 && var.owner_tag != "unapproved" && var.owner_tag != "" && can(regex("Z$", var.stop_at_utc)) && can(timeadd(var.stop_at_utc, "0s"))
      error_message = "Record a positive approved budget, named owner, and RFC3339 stop time before enabling."
    }
    precondition {
      condition = try(
        timecmp(var.stop_at_utc, plantimestamp()) > 0 &&
        timecmp(var.stop_at_utc, timeadd(plantimestamp(), "4h")) <= 0,
        false
      )
      error_message = "stop_at_utc must be in the future and no more than four hours after this plan."
    }
    precondition {
      condition     = try(timecmp(var.stop_at_utc, timestamp()) > 0, false)
      error_message = "stop_at_utc expired before apply; stop and make a new approved plan."
    }
  }
}

module "vpc" {
  count   = var.enable_experiment ? 1 : 0
  source  = "terraform-aws-modules/vpc/aws"
  version = "6.7.3"

  name = "${var.cluster_name}-vpc"
  cidr = "10.70.0.0/16"
  azs  = slice(data.aws_availability_zones.available[0].names, 0, 2)

  private_subnets = ["10.70.0.0/20", "10.70.16.0/20"]
  public_subnets  = ["10.70.32.0/20", "10.70.48.0/20"]

  enable_dns_hostnames = true
  enable_nat_gateway   = true
  single_nat_gateway   = true

  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = "1"
  }
  public_subnet_tags = {
    "kubernetes.io/role/elb" = "1"
  }

  depends_on = [terraform_data.approval_gate]
}

module "eks" {
  count   = var.enable_experiment ? 1 : 0
  source  = "terraform-aws-modules/eks/aws"
  version = "21.25.0"

  name               = var.cluster_name
  kubernetes_version = "1.34"
  vpc_id             = module.vpc[0].vpc_id
  subnet_ids         = module.vpc[0].private_subnets

  endpoint_private_access                  = true
  endpoint_public_access                   = true
  endpoint_public_access_cidrs             = [var.operator_cidr]
  enable_cluster_creator_admin_permissions = true

  # The disposable compatibility pilot omits optional billable integrations.
  # Reassess control-plane logs, encryption, and IRSA for any real use.
  enabled_log_types           = []
  create_cloudwatch_log_group = false
  encryption_config           = null
  create_kms_key              = false
  enable_irsa                 = false

  eks_managed_node_groups = {
    pilot = {
      instance_types = ["m6i.large"]
      capacity_type  = "ON_DEMAND"
      ami_type       = "AL2023_x86_64_STANDARD"
      min_size       = 2
      max_size       = 2
      desired_size   = 2
      # disk_size is ignored when the module's custom launch template is used.
      create_launch_template     = false
      use_custom_launch_template = false
      disk_size                  = 20
    }
  }

  depends_on = [terraform_data.approval_gate]
}

output "planned_cluster_name" {
  value = var.enable_experiment ? module.eks[0].cluster_name : null
}

output "cleanup_deadline_utc" {
  value = var.enable_experiment ? var.stop_at_utc : null
}
