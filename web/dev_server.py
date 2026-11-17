"""Serve AcciAlert dashboard, modular web GIS, and proxy /geoserver/* to local GeoServer.

Supports:
- HTTP on 0.0.0.0:8000 (Localhost, USB debugging, LAN HTTP)
- HTTPS on 0.0.0.0:8443 (Secure Context for Mobile Phone GPS over Wi-Fi)
"""

import datetime
import ipaddress
import mimetypes
import os
import socket
import ssl
import sys
import threading
from http.client import HTTPConnection, HTTPException
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VIS_ROOT = PROJECT_ROOT / "outputs" / "road_aligned_analysis" / "visualization"
WEB_ROOT = PROJECT_ROOT / "web"
SSL_DIR = WEB_ROOT / ".ssl"
GEOSERVER_HOST = "127.0.0.1"
GEOSERVER_PORT = 8080
HTTP_PORT = 8000
HTTPS_PORT = 8443


def get_lan_ips():
    """Detect local LAN IPv4 addresses (Wi-Fi, Ethernet)."""
    ips = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and not ip.startswith("169.254."):
                ips.add(ip)
    except Exception:
        pass
    return sorted(list(ips)) or ["127.0.0.1"]


def get_or_create_ssl_cert(lan_ips):
    """Generate a self-signed certificate with SANs for localhost and LAN IPs."""
    SSL_DIR.mkdir(parents=True, exist_ok=True)
    cert_path = SSL_DIR / "cert.pem"
    key_path = SSL_DIR / "key.pem"

    if cert_path.is_file() and key_path.is_file():
        return cert_path, key_path

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "AcciAlert Mobile Dev"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "AcciAlert"),
        ])
        sans = [
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        ]
        for ip in lan_ips:
            try:
                sans.append(x509.IPAddress(ipaddress.IPv4Address(ip)))
            except Exception:
                pass

        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1))
            .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365))
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .sign(key, hashes.SHA256())
        )

        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        return cert_path, key_path
    except Exception as e:
        print(f"[Warning] Could not auto-generate SSL cert: {e}")
        return None, None


class AcciAlertHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        # Development mode: prevent browser caching of HTML, JS, CSS, and GeoJSON
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def _proxy_geoserver(self):
        upstream = HTTPConnection(GEOSERVER_HOST, GEOSERVER_PORT, timeout=45)
        headers = {
            key: value for key, value in self.headers.items()
            if key.lower() not in {"host", "connection", "content-length", "accept-encoding"}
        }
        body = self.rfile.read(int(self.headers.get("Content-Length", "0"))) if self.command in {"POST", "PUT"} else None
        try:
            upstream.request(self.command, self.path, body=body, headers=headers)
            response = upstream.getresponse()
            payload = response.read()
            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                if key.lower() not in {"connection", "transfer-encoding", "content-length", "content-encoding"}:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
        except (OSError, HTTPException) as error:
            message = (
                f"GeoServer proxy error: {error}\n"
                f"Make sure GeoServer is running on {GEOSERVER_HOST}:{GEOSERVER_PORT}."
            ).encode("utf-8", errors="replace")
            self.send_response(502, "Bad Gateway")
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(message)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(message)
        finally:
            upstream.close()

    def _serve_file(self, file_path: Path):
        if not file_path.is_file():
            self.send_error(404, f"File not found: {file_path.name}")
            return
        content_type, _ = mimetypes.guess_type(str(file_path))
        if content_type is None:
            content_type = "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/json", "application/javascript"}:
            content_type += "; charset=utf-8"

        try:
            data = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
        except Exception as e:
            self.send_error(500, f"Error reading file: {e}")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        # 1. GeoServer proxy
        if path.startswith("/geoserver/") or path == "/geoserver":
            self._proxy_geoserver()
            return

        # 2. Main monolithic dashboard: / or /traffic_hotspot_dashboard.html
        if path in {"/", "/traffic_hotspot_dashboard.html"}:
            self._serve_file(VIS_ROOT / "traffic_hotspot_dashboard.html")
            return

        # 3. Folium map: /hotspots_on_osm.html
        if path == "/hotspots_on_osm.html":
            self._serve_file(VIS_ROOT / "hotspots_on_osm.html")
            return

        # 4. Modular web GIS application: /web, /web/, /web/index.html, /index.html
        if path in {"/web", "/web/", "/web/index.html", "/index.html"}:
            self._serve_file(WEB_ROOT / "index.html")
            return

        # 5. Assets under /web/* (e.g. /web/js/app.js, /web/css/styles.css, /web/favicon.svg)
        if path.startswith("/web/"):
            relative = path[len("/web/"):]
            self._serve_file(WEB_ROOT / relative)
            return

        # 6. Direct assets under /js/* or /css/* or /favicon.svg
        if path.startswith("/js/") or path.startswith("/css/") or path == "/favicon.svg":
            relative = path.lstrip("/")
            self._serve_file(WEB_ROOT / relative)
            return

        # 7. Outputs directory: /outputs/*
        if path.startswith("/outputs/"):
            relative = path[len("/outputs/"):]
            self._serve_file(PROJECT_ROOT / "outputs" / relative)
            return

        # 8. Look in VIS_ROOT, then WEB_ROOT, then PROJECT_ROOT
        cand_vis = VIS_ROOT / path.lstrip("/")
        if cand_vis.is_file():
            self._serve_file(cand_vis)
            return

        cand_web = WEB_ROOT / path.lstrip("/")
        if cand_web.is_file():
            self._serve_file(cand_web)
            return

        cand_proj = PROJECT_ROOT / path.lstrip("/")
        if cand_proj.is_file():
            self._serve_file(cand_proj)
            return

        self.send_error(404, f"Path not found: {path}")

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        if self.path.startswith("/geoserver/") or self.path == "/geoserver":
            self._proxy_geoserver()
        else:
            self.send_error(405, "POST is only proxied to GeoServer")

    def log_message(self, format_string, *args):
        print("[%s] %s" % (self.log_date_time_string(), format_string % args))


if __name__ == "__main__":
    lan_ips = get_lan_ips()
    primary_lan_ip = lan_ips[0]

    # 1. Setup HTTP server on 0.0.0.0:8000
    http_server = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), AcciAlertHandler)

    # 2. Setup HTTPS server on 0.0.0.0:8443
    cert_path, key_path = get_or_create_ssl_cert(lan_ips)
    https_server = None
    if cert_path and key_path:
        try:
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
            https_server = ThreadingHTTPServer(("0.0.0.0", HTTPS_PORT), AcciAlertHandler)
            https_server.socket = ssl_context.wrap_socket(https_server.socket, server_side=True)
        except Exception as e:
            print(f"[Warning] Could not initialize HTTPS server: {e}")
            https_server = None

    print("=" * 70)
    print("AcciAlert Dual Development Server (HTTP + HTTPS for Mobile GPS)")
    print("=" * 70)
    print("🖥️  DESKTOP ACCESS:")
    print(f"   Dashboard: http://localhost:{HTTP_PORT}/")
    print(f"   Web GIS:   http://localhost:{HTTP_PORT}/web/")
    print()
    print("📱 MOBILE PHONE ACCESS (Required for Real Device GPS):")
    if https_server:
        print(f"   🔥 Option A (Recommended - Wi-Fi HTTPS with full GPS support):")
        print(f"      https://{primary_lan_ip}:{HTTPS_PORT}/")
        print("      (Browser will show self-signed warning: tap Advanced -> Proceed)")
    print()
    print(f"   🔌 Option B (USB Cable via Chrome Port Forwarding):")
    print(f"      1. Connect phone via USB with USB Debugging enabled.")
    print(f"      2. In PC Chrome: chrome://inspect/#devices -> Port forwarding -> 8000:8000.")
    print(f"      3. Open on phone: http://localhost:{HTTP_PORT}/")
    print()
    print(f"   🌐 Option C (Wi-Fi HTTP):")
    print(f"      http://{primary_lan_ip}:{HTTP_PORT}/")
    print(f"      (Note: Chrome requires HTTPS for GPS; see Option A or chrome://flags)")
    print()
    print(f"🗺️  GeoServer Proxy: -> http://{GEOSERVER_HOST}:{GEOSERVER_PORT}/geoserver/")
    print("Press Ctrl+C to stop.")
    print("=" * 70)

    threads = []
    t_http = threading.Thread(target=http_server.serve_forever, daemon=True)
    t_http.start()
    threads.append(t_http)

    if https_server:
        t_https = threading.Thread(target=https_server.serve_forever, daemon=True)
        t_https.start()
        threads.append(t_https)

    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("\nStopping servers...")
    finally:
        http_server.server_close()
        if https_server:
            https_server.server_close()
        print("Servers stopped.")
