resource "google_compute_network" "this" {
  name                    = "${var.name_prefix}-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "this" {
  name          = "${var.name_prefix}-subnet"
  ip_cidr_range = var.subnet_cidr
  network       = google_compute_network.this.id
}

resource "google_compute_firewall" "ssh" {
  count   = var.allowed_ssh_cidr == "" ? 0 : 1
  name    = "${var.name_prefix}-allow-ssh"
  network = google_compute_network.this.name

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = [var.allowed_ssh_cidr]
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
