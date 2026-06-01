resource "google_compute_network" "this" {
  name                    = "${var.name_prefix}-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "this" {
  name                     = "${var.name_prefix}-subnet"
  ip_cidr_range            = var.subnet_cidr
  network                  = google_compute_network.this.id
  private_ip_google_access = true
}

# Cloud NAT gives the private-only nodes outbound internet for package installs
# at boot. It is egress-only: it does not allow any inbound traffic from the internet.
# The workflow applies with enable_egress=true to install software, then re-applies
# with enable_egress=false to remove NAT and leave the nodes with no internet access.
resource "google_compute_router" "this" {
  count   = var.enable_egress ? 1 : 0
  name    = "${var.name_prefix}-router"
  network = google_compute_network.this.id
}

resource "google_compute_router_nat" "this" {
  count                              = var.enable_egress ? 1 : 0
  name                               = "${var.name_prefix}-nat"
  router                             = google_compute_router.this[0].name
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
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
