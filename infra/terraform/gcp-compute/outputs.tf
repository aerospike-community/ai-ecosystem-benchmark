output "endpoints" {
  description = "Non-sensitive endpoint details for enabled backends. Hosts are private VPC IPs."
  value = {
    redis = var.enable_redis ? {
      host           = module.redis[0].primary_internal_ip
      port           = 6379
      url            = "redis://${module.redis[0].primary_internal_ip}:6379/0"
      topology       = module.redis[0].topology
      sentinel_hosts = module.redis[0].sentinel_hosts
      sentinel_port  = module.redis[0].topology == "sentinel" ? 26379 : null
      nodes          = module.redis[0].nodes
    } : null

    postgres = var.enable_postgres ? {
      host                    = module.postgres[0].primary_internal_ip
      port                    = 5432
      database                = var.postgres_db_name
      username                = var.postgres_db_user
      connection_string       = "postgresql://${var.postgres_db_user}:<password>@${module.postgres[0].primary_internal_ip}:5432/${var.postgres_db_name}"
      async_connection_string = "postgresql+asyncpg://${var.postgres_db_user}:<password>@${module.postgres[0].primary_internal_ip}:5432/${var.postgres_db_name}"
      topology                = module.postgres[0].topology
      replica_hosts           = module.postgres[0].replica_internal_ips
      nodes                   = module.postgres[0].nodes
    } : null

    aerospike = var.enable_aerospike ? {
      host              = module.aerospike[0].seed_internal_ip
      port              = 3000
      connection_string = "${module.aerospike[0].seed_internal_ip}:3000"
      nodes             = module.aerospike[0].nodes
    } : null
  }
}

output "network" {
  description = "Benchmark VPC details."
  value = {
    name        = module.network.network_name
    subnet_cidr = module.network.subnet_cidr
  }
}
