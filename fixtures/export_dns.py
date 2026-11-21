"""Export Python reference outputs for the pure DNS helpers (F-017 DNS-1).

Run from the repo root: python fixtures/export_dns.py
Writes fixtures/dns_pure.json.
"""

import json
import random
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "old"))

from cyoa_downloader_app.network import dns  # noqa: E402


def call(fn, *a):
    try:
        return {"ok": fn(*a)}
    except Exception as exc:  # noqa: BLE001 - record Python's error text
        return {"err": str(exc)}


def query(host, qtype, tx):
    orig = random.randint
    random.randint = lambda a, b: tx
    try:
        r = call(dns._build_dns_query_wire, host, qtype)
    finally:
        random.randint = orig
    if "ok" in r:
        r = {"ok": r["ok"][1].hex()}
    return r


def name(n):
    return b"".join(len(p).to_bytes(1, "big") + p for p in (x.encode() for x in n.split("."))) + b"\0"


def resp(tx, answers, qname="example.com", qtype=1, flags=0x8180, qd=1):
    hdr = struct.pack(">HHHHHH", tx, flags, qd, len(answers), 0, 0)
    q = name(qname) + struct.pack(">HH", qtype, 1) if qd else b""
    return hdr + q + b"".join(answers)


def rr(rtype, rdata, owner=b"\xc0\x0c"):
    return owner + struct.pack(">HHIH", rtype, 1, 60, len(rdata)) + rdata


A = rr(1, bytes([93, 184, 216, 34]))
AAAA = rr(28, bytes.fromhex("26062800022000010248189325c81946"))
CNAME = rr(5, name("alias.example.net"))
cases_parse = {
    "a": (resp(0x1234, [A]), 0x1234, 1),
    "aaaa": (resp(0x1234, [AAAA], qtype=28), 0x1234, 28),
    "a_wrong_tx": (resp(0x1234, [A]), 0x9999, 1),
    "a_no_tx": (resp(0x1234, [A]), None, 1),
    "cname_then_a": (resp(1, [CNAME, A]), 1, 1),
    "a_asked_aaaa": (resp(1, [A], qtype=28), 1, 28),
    "short": (b"\x12\x34\x81", 0x1234, 1),
    "truncated_rdata": (resp(1, [A])[:-2], 1, 1),
    "pointer_loop": (resp(1, [rr(1, bytes(4), owner=b"\xc0\x30")]) + b"\xc0\x30", 1, 1),
    "rcode_nxdomain": (resp(1, [A], flags=0x8183), 1, 1),
    "no_answers": (resp(1, []), 1, 1),
    "not_response": (resp(1, [A], flags=0x0100), 1, 1),
    "bad_rdlen_a": (resp(1, [rr(1, bytes(5))]), 1, 1),
    "empty": (b"", None, 1),
}

out = {
    "query": {
        f"{h}|{t}|{tx}": query(h, t, tx)
        for h, t, tx in [
            ("example.com", 1, 0x1234), ("example.com", 28, 0x1234), ("Example.COM.", 1, 7),
            ("bücher.de", 1, 1), ("a..b", 1, 1), ("x" * 64 + ".com", 1, 1),
            (".".join(["a" * 63] * 4), 1, 1), ("", 1, 1), ("localhost", 1, 65535),
        ]
    },
    "parse": {
        k: {"hex": d.hex(), "tx": tx, "qtype": qt, "py": call(dns._parse_dns_address_response, d, tx, qt)}
        for k, (d, tx, qt) in cases_parse.items()
    },
    "split": {
        f"{s}|{p}|{port}": call(dns._split_dns_endpoint, s, p, port)
        for s, p, port in [
            ("1.1.1.1", "udp", 0), ("1.1.1.1:5353", "udp", 0), ("1.1.1.1", "dot", 0),
            ("2606:4700::1111", "udp", 0), ("[2606:4700::1111]:53", "tcp", 0),
            ("tls://one.one.one.one", "dot", 0), ("tls://one.one.one.one:8853", "dot", 0),
            ("tcp://8.8.8.8:5353", "tcp", 0), ("dns.quad9.net", "dot", 0), ("8.8.8.8", "udp", 99),
            (" 9.9.9.9 ", "udp", 0), ("", "udp", 0), ("host:99999", "udp", 0),
        ]
    },
    "infer": {
        f"{s}|{p}": call(dns._infer_dns_protocol, s, p)
        for s, p in [
            ("", None), ("https://dns.google/dns-query", None), ("tls://x", None), ("dot://x", None),
            ("tcp://1.1.1.1", None), ("1.1.1.1", None), ("1.1.1.1", "DoH"), ("1.1.1.1", "bogus"),
            ("HTTPS://X", ""), ("  ", "system"),
        ]
    },
    "validate": {
        f"{s}|{p}": call(dns._validate_dns_configuration, s, p)
        for s, p in [
            ("", "system"), ("", "udp"), ("1.1.1.1", "udp"), ("dns.google", "udp"), ("::1", "tcp"),
            ("https://dns.google/dns-query", "doh"), ("http://dns.google", "doh"), ("https://", "doh"),
            ("https://dns.google:0/q", "doh"), ("1.1.1.1", "dot"), ("one.one.one.one", "dot"),
            ("tls://dns.quad9.net:853", "dot"), ("[::1]:53", "udp"),
        ]
    },
}
path = ROOT / "rust" / "fixtures" / "dns_pure.json"
path.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print("wrote", path)
