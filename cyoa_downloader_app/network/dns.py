"""Custom DNS / DNS-over-HTTPS helpers."""

from __future__ import annotations

import time
from typing import Optional, Tuple

import requests

from ._bridge import legacy


def _build_dns_query_wire(host: str, qtype: int = 1) -> Tuple[int, bytes]:
    """Build a minimal DNS query packet. qtype=1 A, qtype=28 AAAA."""
    import random as _rnd
    import struct

    tx_id = _rnd.randint(0, 65535)
    header = struct.pack(">HHHHHH", tx_id, 0x0100, 1, 0, 0, 0)
    qname = b"".join(
        len(p).to_bytes(1, "big") + p.encode("idna")
        for p in host.rstrip(".").split(".")
    ) + b"\x00"
    return tx_id, header + qname + struct.pack(">HH", qtype, 1)


def _parse_dns_address_response(data: bytes, tx_id: Optional[int] = None, qtype: int = 1) -> Optional[str]:
    """Parse the first A or AAAA answer from a DNS wire response."""
    l = legacy()
    try:
        import struct
        if len(data) < 12:
            return None
        rid, _flags, qdcount, ancount, _nscount, _arcount = struct.unpack(">HHHHHH", data[:12])
        if tx_id is not None and rid != tx_id:
            return None
        offset = 12

        def _skip_name(buf: bytes, off: int) -> int:
            while off < len(buf):
                length = buf[off]
                if length == 0:
                    return off + 1
                if length & 0xC0 == 0xC0:
                    return off + 2
                off += length + 1
            return off

        for _ in range(qdcount):
            offset = _skip_name(data, offset) + 4
        for _ in range(ancount):
            offset = _skip_name(data, offset)
            if offset + 10 > len(data):
                return None
            rtype, rclass, _ttl, rdlen = struct.unpack(">HHIH", data[offset:offset + 10])
            offset += 10
            if offset + rdlen > len(data):
                return None
            if qtype == 1 and rtype == 1 and rclass == 1 and rdlen == 4:
                return ".".join(str(b) for b in data[offset:offset + 4])
            if qtype == 28 and rtype == 28 and rclass == 1 and rdlen == 16:
                import ipaddress
                return str(ipaddress.IPv6Address(data[offset:offset + 16]))
            offset += rdlen
    except Exception as e:
        l.logger.debug(f"DNS response parse failed: {e}")
    return None


def _doh_resolve_via(host: str, doh_url: str, qtype: int = 1) -> Optional[str]:
    """Resolve host through a DNS-over-HTTPS endpoint using DNS wire format."""
    l = legacy()
    if not doh_url.lower().startswith("https://"):
        return None
    try:
        tx_id, payload = _build_dns_query_wire(host, qtype=qtype)
        headers = {
            "Accept": "application/dns-message",
            "Content-Type": "application/dns-message",
            "User-Agent": "Mozilla/5.0",
        }
        setattr(l._dns_bypass_local, "enabled", True)
        session = None
        try:
            session = requests.Session()
            session.trust_env = (getattr(l, "_proxy_mode", "inherit_env") == "inherit_env")
            proxy = l._get_active_proxy()
            if proxy:
                session.proxies.update({"http": proxy, "https": proxy})
            r = session.post(doh_url, data=payload, headers=headers, timeout=6)
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception as close_exc:
                    l.logger.debug("DoH session close failed: %s", close_exc)
            setattr(l._dns_bypass_local, "enabled", False)
        if r.status_code != 200:
            l.logger.debug(f"DoH {doh_url} returned HTTP {r.status_code} for {host}")
            return None
        return _parse_dns_address_response(r.content, tx_id=tx_id, qtype=qtype)
    except Exception as e:
        try:
            setattr(l._dns_bypass_local, "enabled", False)
        except Exception as exc:
            l.logger.debug("Ignored recoverable exception in _doh_resolve_via: %s", exc)
        l.logger.debug(f"DoH resolve failed for {host} via {doh_url}: {e}")
        return None


def _dns_resolve_via(host: str, dns_ip: str, qtype: int = 1) -> Optional[str]:
    """Resolve host using plain DNS or DNS-over-HTTPS with a short cache."""
    l = legacy()
    cache_key = (host.lower().rstrip("."), str(dns_ip), int(qtype))
    now = time.time()
    cached = l._dns_cache.get(cache_key)
    if cached and cached[0] > now:
        return cached[1]

    def _store(ip: Optional[str]) -> Optional[str]:
        if ip:
            l._dns_cache[cache_key] = (time.time() + l._DNS_CACHE_TTL_SECONDS, ip)
        return ip

    if (dns_ip or "").lower().startswith("https://"):
        return _store(_doh_resolve_via(host, dns_ip, qtype=qtype))

    try:
        import dns.resolver as _dr  # type: ignore
        r = _dr.Resolver(configure=False)
        r.nameservers = [dns_ip]
        r.timeout = 3
        r.lifetime = 5
        answers = r.resolve(host, "AAAA" if qtype == 28 else "A")
        return _store(str(answers[0]))
    except ImportError as exc:
        l.logger.debug("Ignored recoverable exception in _dns_resolve_via: %s", exc)
    except Exception as exc:
        l.logger.debug("Ignored recoverable exception in _dns_resolve_via: %s", exc)

    if qtype != 1:
        return None

    try:
        import random as _rnd
        import struct
        tx_id = _rnd.randint(0, 65535)
        header = struct.pack(">HHHHHH", tx_id, 0x0100, 1, 0, 0, 0)
        qname = b"".join(len(p).to_bytes(1, "big") + p.encode() for p in host.rstrip(".").split(".")) + b"\x00"
        packet = header + qname + struct.pack(">HH", 1, 1)
        with l._socket.socket(l._socket.AF_INET, l._socket.SOCK_DGRAM) as sock:
            sock.settimeout(3)
            sock.sendto(packet, (dns_ip, 53))
            data, _ = sock.recvfrom(512)
        if len(data) < 12 or struct.unpack(">H", data[:2])[0] != tx_id:
            return None
        ancount = struct.unpack(">H", data[6:8])[0]
        if ancount == 0:
            return None
        offset = 12
        while data[offset] != 0:
            if data[offset] & 0xC0 == 0xC0:
                offset += 2
                break
            offset += data[offset] + 1
        else:
            offset += 1
        offset += 4
        if data[offset] & 0xC0 == 0xC0:
            offset += 2
        else:
            while data[offset] != 0:
                offset += data[offset] + 1
            offset += 1
        rtype, _, _, rdlen = struct.unpack(">HHIH", data[offset:offset + 10])
        offset += 10
        if rtype == 1 and rdlen == 4:
            return _store(".".join(str(b) for b in data[offset:offset + 4]))
    except Exception as exc:
        l.logger.debug("Ignored recoverable exception in _dns_resolve_via: %s", exc)
    return None


def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    """socket.getaddrinfo override — resolve host via custom DNS or DoH."""
    l = legacy()
    if getattr(l._dns_bypass_local, "enabled", False):
        return l._orig_getaddrinfo(host, port, family, type, proto, flags)
    try:
        l._socket.inet_aton(host)
        return l._orig_getaddrinfo(host, port, family, type, proto, flags)
    except OSError as exc:
        l.logger.debug("Ignored recoverable exception in _patched_getaddrinfo: %s", exc)
    if host in ("localhost", "127.0.0.1", "::1"):
        return l._orig_getaddrinfo(host, port, family, type, proto, flags)
    if not getattr(l, "_active_dns", None):
        return l._orig_getaddrinfo(host, port, family, type, proto, flags)
    try:
        ip = _dns_resolve_via(host, l._active_dns, qtype=1)
        if not ip and family in (0, getattr(l._socket, "AF_INET6", -1)):
            ip = _dns_resolve_via(host, l._active_dns, qtype=28)
        if ip:
            l.logger.debug(f"DNS [{l._active_dns}] {host} → {ip}")
            return l._orig_getaddrinfo(ip, port, family, type, proto, flags)
    except Exception as e:
        l.logger.debug(f"Custom DNS failed for {host}: {e}")
    return l._orig_getaddrinfo(host, port, family, type, proto, flags)


def _set_active_dns(server: Optional[str]) -> None:
    """Set global DNS server and patch/restore socket.getaddrinfo idempotently."""
    l = legacy()
    server = (server or "").strip()
    new_dns = server or None
    desired_getaddrinfo = _patched_getaddrinfo if new_dns else l._orig_getaddrinfo
    if new_dns == getattr(l, "_active_dns", None):
        if l._socket.getaddrinfo is not desired_getaddrinfo:
            l._socket.getaddrinfo = desired_getaddrinfo
        return

    l._active_dns = new_dns
    l._dns_cache.clear()
    if l._active_dns:
        l._socket.getaddrinfo = _patched_getaddrinfo
        if l._active_dns.lower().startswith("https://"):
            l.logger.info(f"DNS-over-HTTPS resolver active → {l._active_dns}")
        else:
            l.logger.info(f"DNS overridden → {l._active_dns}")
    else:
        l._socket.getaddrinfo = l._orig_getaddrinfo
        l.logger.info("DNS restored to system default")
    l._v465_reset_shared_sessions()


def _get_active_dns() -> Optional[str]:
    return getattr(legacy(), "_active_dns", None)
