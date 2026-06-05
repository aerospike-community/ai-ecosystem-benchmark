locals {
  common_labels = merge(
    {
      project    = "ai-ecosystem-benchmark"
      purpose    = "benchmark"
      managed_by = "terraform"
    },
    var.labels,
  )
}

resource "random_password" "postgres_repl" {
  count   = var.enable_postgres ? 1 : 0
  length  = 32
  special = false
}

module "network" {
  source               = "./modules/network"
  name_prefix          = var.name_prefix
  subnet_cidr          = var.subnet_cidr
  enable_local_access  = var.enable_local_access
  enable_egress        = var.enable_egress
  enable_client_egress = var.enable_client
}

module "client" {
  count             = var.enable_client ? 1 : 0
  source            = "./modules/client"
  name_prefix       = var.name_prefix
  zone              = var.zone
  subnet_self_link  = module.network.client_subnet_self_link
  machine_type      = var.client_machine_type
  boot_disk_size_gb = var.client_boot_disk_size_gb
  labels            = local.common_labels
}

module "redis" {
  count            = var.enable_redis ? 1 : 0
  source           = "./modules/redis"
  name_prefix      = var.name_prefix
  topology         = var.redis_topology
  zone             = var.zone
  subnet_self_link = module.network.subnet_self_link
  labels           = local.common_labels
}

module "postgres" {
  count                = var.enable_postgres ? 1 : 0
  source               = "./modules/postgres"
  name_prefix          = var.name_prefix
  topology             = var.postgres_topology
  zone                 = var.zone
  subnet_self_link     = module.network.subnet_self_link
  subnet_cidr          = module.network.subnet_cidr
  db_name              = var.postgres_db_name
  db_user              = var.postgres_db_user
  db_password          = var.postgres_password
  replication_password = var.enable_postgres ? random_password.postgres_repl[0].result : ""
  labels               = local.common_labels
}

module "aerospike" {
  count              = var.enable_aerospike ? 1 : 0
  source             = "./modules/aerospike-cluster"
  name_prefix        = var.name_prefix
  node_count         = var.aerospike_node_count
  replication_factor = var.aerospike_replication_factor
  namespace          = var.aerospike_namespace
  zone               = var.zone
  subnet_self_link   = module.network.subnet_self_link
  server_version     = var.aerospike_server_version
  labels             = local.common_labels
}
