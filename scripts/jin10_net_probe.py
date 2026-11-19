"""Token-free, stage-by-stage network probe for the Jin10 MCP endpoint.

Stages per resolved address: DNS (A/AAAA) -> TCP 443 -> TLS (SNI = original host, cert verified
against the host name) -> HTTP (GET and an unauthenticated MCP initialize POST, Host header kept).
Every stage has its own timeout. No token is read or sent. Output: one JSON document on stdout.
An HTTP status of any kind (401/405/...) means the HTTP layer was reached.
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import sys
import time
from datetime import datetime, timezone

HOST = "mcp.jin10.com"
PATH = "/mcp"
PORT = 443
TCP_TIMEOUT = float(os.environ.get("PROBE_TCP_TIMEOUT", "8"))
IO_TIMEOUT = float(os.environ.get("PROBE_IO_TIMEOUT", "10"))


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def err(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"[:300]


def resolve() -> dict:
    out = {"at": now(), "A": [], "AAAA": [], "error": None}
    for fam, key in ((socket.AF_INET, "A"), (socket.AF_INET6, "AAAA")):
        try:
            out[key] = sorted({ai[4][0] for ai in socket.getaddrinfo(HOST, PORT, fam, socket.SOCK_STREAM)})
        except socket.gaierror as e:
            out[key + "_error"] = err(e)
    return out


def http_exchange(tls: ssl.SSLSocket, request: bytes) -> dict:
    t = time.monotonic()
    tls.settimeout(IO_TIMEOUT)
    tls.sendall(request)
    data = b""
    try:
        while b"\r\n\r\n" not in data and len(data) < 65536:
            chunk = tls.recv(4096)
            if not chunk:
                break
            data += chunk
    except socket.timeout:
        return {"stage": "HTTP_RESPONSE_TIMEOUT", "ms": int((time.monotonic() - t) * 1000), "bytes": len(data)}
    head = data.split(b"\r\n\r\n", 1)[0].decode("latin-1", "replace").split("\r\n")
    status = head[0] if head else ""
    keep = {}
    for line in head[1:]:
        k, _, v = line.partition(":")
        if k.strip().lower() in ("server", "content-type", "www-authenticate", "allow", "mcp-session-id", "date"):
            keep[k.strip().lower()] = v.strip()[:120] if k.strip().lower() != "mcp-session-id" else "<present>"
    return {"stage": "HTTP_OK" if status.startswith("HTTP/") else "HTTP_GARBLED", "status_line": status[:80],
            "headers": keep, "ms": int((time.monotonic() - t) * 1000)}


def probe_addr(addr: str) -> dict:
    fam = socket.AF_INET6 if ":" in addr else socket.AF_INET
    r = {"addr": addr, "family": "IPv6" if fam == socket.AF_INET6 else "IPv4", "started": now()}
    results = {}
    for name, req in (
        ("get", f"GET {PATH} HTTP/1.1\r\nHost: {HOST}\r\nUser-Agent: net-probe\r\nAccept: */*\r\nConnection: close\r\n\r\n".encode()),
        ("mcp_initialize_no_token", None),
    ):
        if req is None:
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "net-probe", "version": "1"}}}).encode()
            req = (f"POST {PATH} HTTP/1.1\r\nHost: {HOST}\r\nUser-Agent: net-probe\r\nContent-Type: application/json\r\n"
                   f"Accept: application/json, text/event-stream\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body
        s = socket.socket(fam, socket.SOCK_STREAM)
        s.settimeout(TCP_TIMEOUT)
        t = time.monotonic()
        try:
            s.connect((addr, PORT))
        except socket.timeout:
            results[name] = {"stage": "TCP_TIMEOUT", "ms": int((time.monotonic() - t) * 1000)}
            s.close(); continue
        except OSError as e:
            results[name] = {"stage": "TCP_ERROR", "error": err(e), "ms": int((time.monotonic() - t) * 1000)}
            s.close(); continue
        tcp_ms = int((time.monotonic() - t) * 1000)
        ctx = ssl.create_default_context()
        t = time.monotonic()
        try:
            s.settimeout(IO_TIMEOUT)
            tls = ctx.wrap_socket(s, server_hostname=HOST)
        except socket.timeout:
            results[name] = {"stage": "TLS_TIMEOUT", "tcp_ms": tcp_ms, "ms": int((time.monotonic() - t) * 1000)}
            s.close(); continue
        except (ssl.SSLError, OSError) as e:
            results[name] = {"stage": "TLS_ERROR", "tcp_ms": tcp_ms, "error": err(e)}
            s.close(); continue
        cert = tls.getpeercert() or {}
        tls_info = {"tcp_ms": tcp_ms, "tls_ms": int((time.monotonic() - t) * 1000), "tls_version": tls.version(),
                    "cert_subject_cn": dict(x[0] for x in cert.get("subject", ())).get("commonName"),
                    "cert_not_after": cert.get("notAfter")}
        try:
            h = http_exchange(tls, req)
        except OSError as e:
            h = {"stage": "HTTP_ERROR", "error": err(e)}
        finally:
            tls.close()
        results[name] = {**tls_info, **h}
    r["results"] = results
    return r


def main() -> None:
    env = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    proxies = {k: ("<set>" if os.environ.get(k) else None) for k in
               ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy")}
    dns = resolve()
    doc = {"environment": env, "target": f"https://{HOST}{PATH}", "proxy_env": {k: v for k, v in proxies.items() if v},
           "timeouts_s": {"tcp": TCP_TIMEOUT, "io": IO_TIMEOUT}, "dns": dns,
           "probes": [probe_addr(a) for a in dns["A"] + dns["AAAA"]], "finished": now()}
    print(json.dumps(doc, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
