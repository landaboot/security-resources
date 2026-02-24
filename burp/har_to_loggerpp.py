#!/usr/bin/env python3
"""
har_to_loggerpp.py
Converte um arquivo .HAR para o formato JSON compatível com
"Import From Exported JSON" do Logger++ (nccgroup).

Uso:
    python har_to_loggerpp.py <arquivo.har> [saida.json]

Requer apenas a stdlib do Python 3.
"""

import json
import sys
import base64
from pathlib import Path
from urllib.parse import urlparse


# ─── Helpers ─────────────────────────────────────────────────────────────────

def normalize_http_version(v: str) -> str:
    """h2 / h3 / HTTP/2 → HTTP/1.1  (Logger++ works over HTTP/1.1 text)."""
    if not v:
        return "HTTP/1.1"
    u = v.upper().strip()
    if u in ("H2", "H3", "HTTP/2", "HTTP/2.0", "HTTP/3", "HTTP/3.0"):
        return "HTTP/1.1"
    return u if u.startswith("HTTP/") else "HTTP/1.1"


def clean_headers(headers: list) -> list:
    """
    Remove pseudo-headers HTTP/2+ (name starts with ':').
    Promotes ':authority' → 'Host' if no Host is present.
    """
    result = []
    authority = None
    for h in headers:
        name  = h.get("name",  "")
        value = h.get("value", "")
        if name == ":authority":
            authority = value
        elif name.startswith(":"):
            continue          # drop :method, :path, :scheme, :status …
        else:
            result.append((name, value))

    has_host = any(n.lower() == "host" for n, _ in result)
    if not has_host and authority:
        result.insert(0, ("Host", authority))
    return result


def headers_block(headers: list) -> str:
    """Turn [(name, value), …] into 'Name: Value\r\nName: Value'."""
    return "\r\n".join(f"{n}: {v}" for n, v in headers)


def get_body(content: dict) -> bytes:
    """Extract body from a HAR content/postData block as raw bytes."""
    text     = (content or {}).get("text", "") or ""
    encoding = (content or {}).get("encoding", "")
    if encoding == "base64" and text:
        try:
            return base64.b64decode(text)
        except Exception:
            pass
    return text.encode("utf-8", errors="replace")


def build_raw_request(entry: dict) -> bytes:
    """Assemble a valid HTTP/1.1 request message as bytes."""
    req     = entry.get("request", {})
    method  = req.get("method", "GET").upper()
    ver     = normalize_http_version(req.get("httpVersion", "HTTP/1.1"))

    parsed      = urlparse(req.get("url", ""))
    path_query  = (parsed.path or "/") + (("?" + parsed.query) if parsed.query else "")

    hdrs  = clean_headers(req.get("headers", []))
    body  = get_body(req.get("postData") or {})

    lines  = f"{method} {path_query} {ver}\r\n"
    lines += headers_block(hdrs)
    lines += "\r\n\r\n"
    return lines.encode("utf-8", errors="replace") + body


def build_raw_response(entry: dict, req_version: str) -> bytes:
    """Assemble a valid HTTP/1.1 response message as bytes."""
    res         = entry.get("response", {})
    status      = res.get("status",     200)
    status_text = res.get("statusText", "") or ""
    ver         = normalize_http_version(res.get("httpVersion", req_version))

    hdrs = clean_headers(res.get("headers", []))
    body = get_body(res.get("content") or {})

    lines  = f"{ver} {status} {status_text}\r\n"
    lines += headers_block(hdrs)
    lines += "\r\n\r\n"
    return lines.encode("utf-8", errors="replace") + body


# ─── Main conversion ──────────────────────────────────────────────────────────

def convert(har_path: str, out_path: str) -> None:
    src = Path(har_path)
    if not src.exists():
        print(f"[ERRO] Arquivo não encontrado: {har_path}")
        sys.exit(1)

    with open(src, encoding="utf-8") as f:
        har = json.load(f)

    entries = har.get("log", {}).get("entries", [])
    if not entries:
        print("[AVISO] Nenhuma entrada encontrada no HAR.")
        return

    # ── Logger++ "Import From Exported JSON" format ───────────────────────────
    # The importer (LoggerImport.java) reads exactly:
    #   arr[i]["Request"]["URL"]      → used to build HttpService
    #   arr[i]["Request"]["AsBase64"] → raw HTTP request  (base64)
    #   arr[i]["Response"]["AsBase64"]→ raw HTTP response (base64)
    #
    # The exporter (LogEntrySerializer) wraps every LogEntryField under its
    # FieldGroup label ("Entry", "Request", "Response"), so we fill the same
    # structure with all useful fields so exported files are also readable.
    # ─────────────────────────────────────────────────────────────────────────

    output = []
    ok = skipped = 0

    for i, entry in enumerate(entries):
        try:
            req_obj = entry.get("request",  {})
            res_obj = entry.get("response", {})

            url        = req_obj.get("url", "")
            method     = req_obj.get("method", "GET").upper()
            req_ver    = normalize_http_version(req_obj.get("httpVersion", "HTTP/1.1"))
            parsed_url = urlparse(url)

            status      = res_obj.get("status",     0)
            status_text = res_obj.get("statusText", "") or ""
            content     = res_obj.get("content",    {}) or {}

            raw_req  = build_raw_request(entry)
            raw_resp = build_raw_response(entry, req_ver)

            req_b64  = base64.b64encode(raw_req).decode()
            resp_b64 = base64.b64encode(raw_resp).decode()

            # Started time
            started = entry.get("startedDateTime", "")

            record = {
                # ── FieldGroup.ENTRY ─────────────────────────────────────────
                "Entry": {
                    "Tool":            "Proxy",
                    "Tags":            [],
                    "InScope":         False,
                    "ListenInterface": "",
                    "ClientIP":        "",
                },

                # ── FieldGroup.REQUEST ───────────────────────────────────────
                # CRITICAL: "URL" and "AsBase64" are read by the importer
                "Request": {
                    "AsBase64":           req_b64,          # ← required by importer
                    "URL":                url,              # ← required by importer
                    "Method":             method,
                    "Protocol":           parsed_url.scheme.lower(),
                    "Hostname":           parsed_url.hostname or "",
                    "Host":               f"{parsed_url.scheme}://{parsed_url.netloc}",
                    "Port":               parsed_url.port or (443 if parsed_url.scheme == "https" else 80),
                    "Path":               parsed_url.path or "/",
                    "Query":              parsed_url.query or "",
                    "PathQuery":          (parsed_url.path or "/") + (("?" + parsed_url.query) if parsed_url.query else ""),
                    "IsSSL":              parsed_url.scheme.lower() == "https",
                    "HasGetParam":        bool(parsed_url.query),
                    "HasParams":          bool(parsed_url.query),
                    "HasPostParam":       bool((req_obj.get("postData") or {}).get("text", "")),
                    "HasSentCookies":     any(h.get("name","").lower() == "cookie" for h in req_obj.get("headers",[])),
                    "ContentType":        next((h["value"] for h in req_obj.get("headers",[]) if h.get("name","").lower() == "content-type"), ""),
                    "RequestHttpVersion": req_ver,
                    "Time":               started,
                    "Comment":            "",
                    "Complete":           True,
                    "Extension":          Path(parsed_url.path).suffix.lstrip("."),
                    "Referrer":           next((h["value"] for h in req_obj.get("headers",[]) if h.get("name","").lower() == "referer"), ""),
                    "Origin":             next((h["value"] for h in req_obj.get("headers",[]) if h.get("name","").lower() == "origin"), ""),
                },

                # ── FieldGroup.RESPONSE ──────────────────────────────────────
                # CRITICAL: "AsBase64" is read by the importer
                "Response": {
                    "AsBase64":            resp_b64,        # ← required by importer
                    "Status":              status,
                    "StatusText":          status_text,
                    "ContentType":         content.get("mimeType", ""),
                    "Length":              content.get("size", len(raw_resp)),
                    "Time":                started,
                    "MimeType":            content.get("mimeType", "").split(";")[0].strip(),
                    "RTT":                 int(entry.get("time", 0)),
                    "HasSetCookies":       any(h.get("name","").lower() == "set-cookie" for h in res_obj.get("headers",[])),
                    "ResponseHttpVersion": normalize_http_version(res_obj.get("httpVersion", req_ver)),
                },
            }

            output.append(record)
            ok += 1

        except Exception as exc:
            print(f"[AVISO] Entrada {i+1} ignorada: {exc}")
            skipped += 1

    dst = Path(out_path)
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[OK] {ok} entradas convertidas" + (f", {skipped} ignoradas." if skipped else "."))
    print(f"[OK] Arquivo salvo: {dst.resolve()}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    har_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) >= 3 else Path(har_path).stem + "_loggerpp.json"
    convert(har_path, out_path)


if __name__ == "__main__":
    main()
