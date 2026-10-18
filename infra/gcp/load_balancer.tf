# Global external Application Load Balancer -> serverless NEG -> Cloud Run.

locals {
  https_enabled = var.domain != ""
}

resource "google_compute_global_address" "lb" {
  name       = "${var.name_prefix}-lb-ip"
  ip_version = "IPV4"

  depends_on = [google_project_service.required]
}

resource "google_compute_region_network_endpoint_group" "app" {
  name                  = "${var.name_prefix}-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = google_cloud_run_v2_service.app.name
  }
}

resource "google_compute_backend_service" "app" {
  name                  = "${var.name_prefix}-backend"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTPS"
  security_policy       = google_compute_security_policy.app.id

  backend {
    group = google_compute_region_network_endpoint_group.app.id
  }

  log_config {
    enable      = true
    sample_rate = 1.0
  }
}

# Cloud Armor: the server functions (Gemini calls, server-side A*) are public RPC endpoints, so
# they are rate limited per client IP. Everything else (static assets, pages) is allowed.
resource "google_compute_security_policy" "app" {
  name        = "${var.name_prefix}-armor"
  description = "Rate limits the server function RPC endpoints per client IP."
  type        = "CLOUD_ARMOR"

  rule {
    action      = "throttle"
    priority    = 1000
    description = "Server functions: ${var.ai_rate_limit_per_minute} requests per IP per minute"

    match {
      expr {
        expression = "request.path.startsWith('/_serverFn')"
      }
    }

    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"

      rate_limit_threshold {
        count        = var.ai_rate_limit_per_minute
        interval_sec = 60
      }
    }
  }

  rule {
    action      = "allow"
    priority    = 2147483647
    description = "Default allow"

    match {
      versioned_expr = "SRC_IPS_V1"

      config {
        src_ip_ranges = ["*"]
      }
    }
  }

  depends_on = [google_project_service.required]
}

resource "google_compute_url_map" "app" {
  name            = "${var.name_prefix}-urlmap"
  default_service = google_compute_backend_service.app.id
}

# HTTPS (only when a domain is configured).

resource "google_compute_managed_ssl_certificate" "app" {
  count = local.https_enabled ? 1 : 0

  name = "${var.name_prefix}-cert"

  managed {
    domains = [var.domain]
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "google_compute_target_https_proxy" "app" {
  count = local.https_enabled ? 1 : 0

  name             = "${var.name_prefix}-https-proxy"
  url_map          = google_compute_url_map.app.id
  ssl_certificates = [google_compute_managed_ssl_certificate.app[0].id]
}

resource "google_compute_global_forwarding_rule" "https" {
  count = local.https_enabled ? 1 : 0

  name                  = "${var.name_prefix}-https"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  ip_protocol           = "TCP"
  ip_address            = google_compute_global_address.lb.id
  port_range            = "443"
  target                = google_compute_target_https_proxy.app[0].id
}

# HTTP: redirect to HTTPS when a domain is configured, otherwise serve the app directly.

resource "google_compute_url_map" "http_redirect" {
  count = local.https_enabled ? 1 : 0

  name = "${var.name_prefix}-http-redirect"

  default_url_redirect {
    https_redirect         = true
    redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"
    strip_query            = false
  }
}

resource "google_compute_target_http_proxy" "app" {
  name    = "${var.name_prefix}-http-proxy"
  url_map = local.https_enabled ? google_compute_url_map.http_redirect[0].id : google_compute_url_map.app.id
}

resource "google_compute_global_forwarding_rule" "http" {
  name                  = "${var.name_prefix}-http"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  ip_protocol           = "TCP"
  ip_address            = google_compute_global_address.lb.id
  port_range            = "80"
  target                = google_compute_target_http_proxy.app.id
}
