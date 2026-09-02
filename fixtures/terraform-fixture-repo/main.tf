## Trivial, safe Terraform for Seam 3 (terramate-api-wrapper#22).
##
## This is deliberately NOT a real Terramate/cloud resource — the point of
## this fixture repo is only to prove the API's PR -> merge -> apply ->
## output-write -> poll loop end to end, per architecture.md §3.1. `random_id`
## and `local_file` are local-only providers: no cloud credentials, no
## billable resources, nothing that outlives the CI runner's ephemeral disk.
##
## `workspace_id` stands in for the real apply-derived output (e.g. a real
## Databricks workspace id) a `workspace` Recipe's `create` Step would
## produce (architecture.md §5.1, §15.1).

terraform {
  required_version = ">= 1.5"
  required_providers {
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
  }
}

resource "random_id" "workspace" {
  byte_length = 4
}

resource "local_file" "provisioned_marker" {
  filename = "${path.module}/.terraform-fixture-marker.json"
  content = jsonencode({
    workspace_id = "ws-${random_id.workspace.hex}"
  })
}

output "workspace_id" {
  value = "ws-${random_id.workspace.hex}"
}
