globals {
  tags = tm_merge(global.global_tags, {
    service_data = "env=SBX"
  })
  # VPC configuration derived dynamically per environment
  vpc_name                      = "edh-dbx-ws-npd"
  route_table_name              = "edh-dbx-ws-npd Private RT"
  vpc_cidr_block_workspace_subnets = "10.185.24.0/21"
  # Number of Availability Zones (subnets) each business domain receives.
  dbx_workspace_subnet_az_count = 2
  privatelink_subnets = [
    "edh-dbx-ws-npd Private A",
    "edh-dbx-ws-npd Private B"
  ]
  # Databricks credentials are sourced exclusively from this Secrets Manager secret.
  databricks_mws_secret_name = "edh/${global.tags.environment}/databricks/edh_iac_sp"

  stack_ids = {
    network_foundation = "ba74849b-ff01-4701-a1f0-8156f21718de"
    # business_domain/workspace stacks: "<domain_name>_workspace"
    controltower_workspace = "3f23efb8-da83-478a-bdb3-23d6b78b38a3"
    # business_domain/workspace_folder stacks: "<domain_name>_workspace_folder"
    controltower_workspace_folder = "7345ad70-d74e-415c-b4f7-2864dd9c656a"
    # data_domain/unity_catalog stacks: "<domain_name>_unity_catalog"
    controltower_unity_catalog = "2f573318-e527-48e4-90d9-9c6f9dbf7a8f"
    # data_domain/unity_catalog_schema stacks: "<catalog_name>_unity_catalog_schema"
    controltower_sbx_unity_catalog_schema    = "40f02f32-e60c-4dd8-8579-2eeb46519579"
    controltower_ai_sbx_unity_catalog_schema = "6336c56e-285f-445c-99b0-5c152e473b14"
  }
}

globals "aws_backend" {
  bucket = "${global.system}-${global.tags.environment}-${global.aws.region}-terraform-state"
  region = "${global.aws.region}"
}
