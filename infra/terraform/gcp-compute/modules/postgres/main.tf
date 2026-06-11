locals {
  node_count = var.topology == "replicated" ? 3 : 1

  node_names = [
    for i in range(local.node_count) : "${var.name_prefix}-postgres-${i + 1}"
  ]

  node_roles = [
    for i in range(local.node_count) : (
      var.topology == "standalone" ? "standalone" : (i == 0 ? "primary" : "replica")
    )
  ]
}

resource "terraform_data" "init_inputs" {
  input = {
    db_name                = var.db_name
    db_password            = var.db_password
    db_user                = var.db_user
    local_ssd_count        = var.local_ssd_count
    postgres_major_version = var.postgres_major_version
    postgres_client_cidrs  = var.client_cidrs
    max_connections        = var.max_connections
    replication_password   = var.replication_password
    replication_user       = var.replication_user
    startup_script_sha     = filesha256("${path.module}/startup.sh")
    synchronous_commit     = var.synchronous_commit
    topology               = var.topology
  }
}

resource "google_compute_instance" "node" {
  count        = local.node_count
  name         = local.node_names[count.index]
  machine_type = var.machine_type
  zone         = var.zone
  tags         = ["bench-node", "postgres"]
  labels       = merge(var.labels, { role = "postgres" })

  boot_disk {
    initialize_params {
      image = var.boot_image
      size  = 50
      type  = "pd-balanced"
    }
  }

  dynamic "scratch_disk" {
    for_each = range(var.local_ssd_count)
    content {
      interface = "NVME"
    }
  }

  network_interface {
    subnetwork = var.subnet_self_link
  }

  metadata = {
    enable-guest-attributes  = "TRUE"
    name-prefix              = var.name_prefix
    node-index               = count.index
    node-role                = local.node_roles[count.index]
    topology                 = var.topology
    postgres-major-version   = var.postgres_major_version
    postgres-db-name         = var.db_name
    postgres-user            = var.db_user
    postgres-password        = var.db_password
    replication-user         = var.replication_user
    replication-password     = var.replication_password
    synchronous-commit       = var.synchronous_commit
    postgres-max-connections = var.max_connections
    postgres-client-cidrs    = join(",", var.client_cidrs)
    vpc-cidr                 = var.subnet_cidr
  }

  metadata_startup_script = file("${path.module}/startup.sh")

  lifecycle {
    replace_triggered_by = [terraform_data.init_inputs]
  }
}
