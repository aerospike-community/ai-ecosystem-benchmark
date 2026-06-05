resource "terraform_data" "init_inputs" {
  input = {
    startup_script_sha = filesha256("${path.module}/startup.sh")
  }
}

resource "google_compute_instance" "this" {
  name         = "${var.name_prefix}-client"
  machine_type = var.machine_type
  zone         = var.zone
  tags         = ["bench-node", "bench-client"]
  labels       = merge(var.labels, { role = "client" })

  boot_disk {
    initialize_params {
      image = var.boot_image
      size  = var.boot_disk_size_gb
      type  = "hyperdisk-balanced"
    }
  }

  network_interface {
    subnetwork = var.subnet_self_link
  }

  metadata = {
    enable-guest-attributes = "TRUE"
    name-prefix             = var.name_prefix
  }

  metadata_startup_script = file("${path.module}/startup.sh")

  lifecycle {
    replace_triggered_by = [terraform_data.init_inputs]
  }
}
