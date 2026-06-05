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

resource "google_compute_subnetwork" "client" {
  count                    = var.enable_client_egress ? 1 : 0
  name                     = "${var.name_prefix}-client-subnet"
  ip_cidr_range            = var.client_subnet_cidr
  network                  = google_compute_network.this.id
  private_ip_google_access = true
}

locals {
  nat_subnet_self_links = merge(
    var.enable_egress ? { backend = google_compute_subnetwork.this.self_link } : {},
    var.enable_client_egress ? { client = google_compute_subnetwork.client[0].self_link } : {},
  )
}

# Cloud NAT is scoped by subnet: backend nodes get temporary egress during
# provisioning, while the client subnet keeps egress for project downloads.
resource "google_compute_router" "this" {
  count   = var.enable_egress || var.enable_client_egress ? 1 : 0
  name    = "${var.name_prefix}-router"
  network = google_compute_network.this.id
}

resource "google_compute_router_nat" "this" {
  count                              = var.enable_egress || var.enable_client_egress ? 1 : 0
  name                               = "${var.name_prefix}-nat"
  router                             = google_compute_router.this[0].name
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"

  dynamic "subnetwork" {
    for_each = local.nat_subnet_self_links

    content {
      name                    = subnetwork.value
      source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
    }
  }
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

  source_ranges = concat(
    [google_compute_subnetwork.this.ip_cidr_range],
    var.enable_client_egress ? [google_compute_subnetwork.client[0].ip_cidr_range] : [],
  )
  target_tags   = ["bench-node"]
}
