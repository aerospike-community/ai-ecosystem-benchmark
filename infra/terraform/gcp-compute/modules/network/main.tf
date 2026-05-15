resource "google_compute_network" "this" {
  name                    = "${var.name_prefix}-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "this" {
  name          = "${var.name_prefix}-subnet"
  ip_cidr_range = var.subnet_cidr
  network       = google_compute_network.this.id
}

resource "google_compute_firewall" "iap_ssh" {
  count   = var.enable_local_access ? 1 : 0
  name    = "${var.name_prefix}-allow-iap-ssh"
  network = google_compute_network.this.name

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["bench-node"]
}

resource "google_compute_firewall" "intra_vpc" {
  name    = "${var.name_prefix}-allow-intra-vpc"
  network = google_compute_network.this.name

  allow {
    protocol = "tcp"
  }

  allow {
    protocol = "udp"
  }

  allow {
    protocol = "icmp"
  }

  source_ranges = [google_compute_subnetwork.this.ip_cidr_range]
  target_tags   = ["bench-node"]
}
