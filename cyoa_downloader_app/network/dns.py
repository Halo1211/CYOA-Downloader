"""Custom DNS / DNS-over-HTTPS helpers."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlsplit

import requests

from ..core.progress import DownloadCancelledError
from ..runtime import state
from ..runtime.compat import mirror_to_legacy
from ._bridge import legacy
from .proxy import _get_active_proxies, _should_bypass_manual_proxy


def _build_dns_query_wire(host: str, qtype: int = 1) -> tuple[int, bytes]:
    """Build a minimal DNS query packet. qtype=1 A, qtype=28 AAAA."""
    import random as _rnd
    import struct

    tx_id = _rnd.randint(0, 65535)
    header = struct.pack(">HHHHHH", tx_id, 0x0100, 1, 0, 0, 0)
    labels = [p.encode("idna") for p in host.rstrip(".").split(".")]
    if any(not p or len(p) > 63 for p in labels):
        raise ValueError("DNS hostname contains an invalid label")
    qname = b"".join(len(p).to_bytes(1, "big") + p for p in labels) + b"\x00"
    if len(qname) > 255:
        raise ValueError("DNS hostname exceeds the wire-format limit")
    return tx_id, header + qname + struct.pack(">HH", qtype, 1)


def _parse_dns_address_response(data: bytes, tx_id: int | None = None, qtype: int = 1) -> str | None:
    """Parse the first A or AAAA answer from a DNS wire response."""
    l = legacy()
    try:
        import struct
        if len(data) < 12:
            return None
        rid, flags, qdcount, ancount, _nscount, _arcount = struct.unpack(">HHHHHH", data[:12])
        if tx_id is not None and rid != tx_id:
            return None
        if not flags & 0x8000 or flags & 0x0200 or flags & 0x000F:
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
    except (IndexError, OSError, UnicodeError, ValueError, struct.error) as e:
        l.logger.debug(f"DNS response parse failed: {e}")
    return None


def _doh_resolve_via(
    host: str, doh_url: str, qtype: int = 1, timeout: int | None = None,
) -> str | None:
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
        previous_bypass = getattr(l._dns_bypass_local, "enabled", False)
        l._dns_bypass_local.enabled = True
        session = None
        try:
            session = requests.Session()
            session.trust_env = (state._proxy_mode == "inherit_env")
            session.proxies.update(_get_active_proxies())
            request_kwargs = {}
            if _should_bypass_manual_proxy(doh_url):
                request_kwargs["proxies"] = {
                    "http": None,
                    "https": None,
                    "all": None,
                }
            r = session.post(
                doh_url, data=payload, headers=headers,
                timeout=max(1, int(timeout or state._dns_timeout)),
                **request_kwargs,
            )
        finally:
            try:
                if session is not None:
                    try:
                        session.close()
                    except DownloadCancelledError:
                        raise
                    except (OSError, requests.RequestException, RuntimeError, TypeError, ValueError) as close_exc:
                        l.logger.debug("DoH session close failed: %s", close_exc)
            finally:
                l._dns_bypass_local.enabled = previous_bypass
        if r.status_code != 200:
            l.logger.debug(f"DoH {doh_url} returned HTTP {r.status_code} for {host}")
            return None
        return _parse_dns_address_response(r.content, tx_id=tx_id, qtype=qtype)
    except (OSError, requests.RequestException, TypeError, ValueError) as e:
        l.logger.debug(f"DoH resolve failed for {host} via {doh_url}: {e}")
        return None


def _split_dns_endpoint(server: str, protocol: str, port: int = 0) -> tuple[str, int]:
    """Return a plain DNS host and effective port for UDP/TCP/DoT."""
    text = str(server or "").strip()
    default_port = 853 if protocol == "dot" else 53
    if "://" in text:
        parsed = urlsplit(text)
        host = parsed.hostname or ""
        parsed_port = parsed.port or 0
    else:
        # A bare IPv6 address contains colons but no port. urlsplit would
        # interpret its final segment as a malformed port unless it is first
        # recognised as an IP literal.
        try:
            import ipaddress
            host = str(ipaddress.ip_address(text.strip("[]")))
            parsed_port = 0
        except ValueError:
            # urlsplit needs // to parse host:port without treating host as a
            # scheme. Brackets remain required for IPv6-with-port.
            parsed = urlsplit("//" + text)
            host = parsed.hostname or text
            parsed_port = parsed.port or 0
    return host.strip("[]"), int(port or parsed_port or default_port)


def _infer_dns_protocol(server: str, protocol: str | None = None) -> str:
    value = str(protocol or "").strip().lower()
    if value in {"system", "udp", "tcp", "doh", "dot"}:
        return value
    endpoint = str(server or "").strip().lower()
    if not endpoint:
        return "system"
    if endpoint.startswith("https://"):
        return "doh"
    if endpoint.startswith(("tls://", "dot://")):
        return "dot"
    if endpoint.startswith("tcp://"):
        return "tcp"
    return "udp"


def _validate_dns_configuration(server: str, protocol: str) -> None:
    """Validate a DNS transport/endpoint pair without sending a query."""
    import ipaddress

    endpoint_text = str(server or "").strip()
    if protocol == "system":
        return
    if not endpoint_text:
        raise ValueError(f"DNS protocol {protocol} requires a resolver endpoint")
    if protocol == "doh":
        parsed = urlsplit(endpoint_text)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise ValueError("DoH requires a full https:// resolver URL")
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError("DoH resolver port must be between 1 and 65535")
        return
    endpoint, _query_port = _split_dns_endpoint(endpoint_text, protocol)
    if not endpoint:
        raise ValueError("DNS resolver endpoint is empty")
    if protocol in {"udp", "tcp"}:
        try:
            ipaddress.ip_address(endpoint)
        except ValueError as exc:
            raise ValueError(
                "UDP/TCP DNS endpoint must be an IPv4 or IPv6 address"
            ) from exc
    if protocol == "dot":
        try:
            ipaddress.ip_address(endpoint)
        except ValueError:
            return
        raise ValueError(
            "DoT requires a hostname for TLS certificate verification; "
            "use one.one.one.one or dns.quad9.net instead of a bare IP"
        )


def _resolve_dot_bootstrap(hostname: str, port: int) -> str:
    """Resolve a DoT server hostname with the OS resolver before TLS starts.

    ``dns.query.tls()`` expects an address for its ``where`` argument and a
    hostname separately for certificate verification.  Using the original OS
    resolver here also avoids recursing through our patched ``getaddrinfo``.
    """
    l = legacy()
    previous_bypass = getattr(state._dns_bypass_local, "enabled", False)
    state._dns_bypass_local.enabled = True
    try:
        addresses = l._orig_getaddrinfo(
            hostname, port, 0, l._socket.SOCK_STREAM,
        )
    finally:
        state._dns_bypass_local.enabled = previous_bypass
    for _family, _type, _proto, _canonname, sockaddr in addresses:
        if sockaddr and sockaddr[0]:
            return str(sockaddr[0])
    raise OSError(f"Could not resolve DoT server hostname: {hostname}")


def _dns_resolve_via(
    host: str,
    dns_ip: str,
    qtype: int = 1,
    *,
    protocol: str | None = None,
    port: int | None = None,
    timeout: int | None = None,
) -> str | None:
    """Resolve host using plain DNS or DNS-over-HTTPS with a short cache."""
    l = legacy()
    transport = _infer_dns_protocol(dns_ip, protocol)
    _validate_dns_configuration(dns_ip, transport)
    effective_port = int(port if port is not None else state._dns_port)
    effective_timeout = max(1, int(timeout if timeout is not None else state._dns_timeout))
    cache_key = (
        host.lower().rstrip("."), str(dns_ip), int(qtype),
        transport, effective_port, effective_timeout,
    )
    now = time.time()
    cached = l._dns_cache.get(cache_key)
    if cached and cached[0] > now:
        return cached[1]

    def _store(ip: str | None) -> str | None:
        if ip:
            l._dns_cache[cache_key] = (time.time() + l._DNS_CACHE_TTL_SECONDS, ip)
        return ip

    if transport == "system":
        return None
    if transport == "doh":
        return _store(
            _doh_resolve_via(host, dns_ip, qtype=qtype, timeout=effective_timeout)
        )

    try:
        import dns.exception as _de  # type: ignore
        import dns.message as _dm  # type: ignore
        import dns.query as _dq  # type: ignore
        import dns.rdatatype as _rdt  # type: ignore
        endpoint, query_port = _split_dns_endpoint(dns_ip, transport, effective_port)
        query = _dm.make_query(host, _rdt.AAAA if qtype == 28 else _rdt.A)
        if transport == "tcp":
            answer = _dq.tcp(query, endpoint, port=query_port, timeout=effective_timeout)
        elif transport == "dot":
            # A hostname is required by validation so TLS hostname checking is
            # never silently disabled. dnspython explicitly documents that a
            # missing server_hostname disables hostname verification.
            tls_name = endpoint
            tls_address = _resolve_dot_bootstrap(endpoint, query_port)
            previous_bypass = getattr(state._dns_bypass_local, "enabled", False)
            state._dns_bypass_local.enabled = True
            try:
                answer = _dq.tls(
                    query, tls_address, port=query_port, timeout=effective_timeout,
                    server_hostname=tls_name,
                )
            finally:
                state._dns_bypass_local.enabled = previous_bypass
        else:
            answer = _dq.udp(query, endpoint, port=query_port, timeout=effective_timeout)
        wanted = _rdt.AAAA if qtype == 28 else _rdt.A
        for rrset in answer.answer:
            if rrset.rdtype == wanted:
                for item in rrset:
                    return _store(str(item))
        return None
    except ImportError as exc:
        l.logger.debug("Ignored recoverable exception in _dns_resolve_via: %s", exc)
    except (_de.DNSException, OSError, RuntimeError, TypeError, ValueError) as exc:
        l.logger.debug("Ignored recoverable exception in _dns_resolve_via: %s", exc)

    # The dependency-free fallback is intentionally UDP-only. Silently
    # downgrading a requested TCP or TLS transport to UDP would violate the
    # selected privacy/transport policy.
    if qtype not in {1, 28} or transport != "udp":
        return None

    try:
        import ipaddress
        import struct
        tx_id, packet = _build_dns_query_wire(host, qtype=qtype)
        endpoint, query_port = _split_dns_endpoint(dns_ip, transport, effective_port)
        socket_family = l._socket.AF_INET6 if ipaddress.ip_address(endpoint).version == 6 else l._socket.AF_INET
        with l._socket.socket(socket_family, l._socket.SOCK_DGRAM) as sock:
            sock.settimeout(effective_timeout)
            sock.sendto(packet, (endpoint, query_port))
            data, _ = sock.recvfrom(512)
        return _store(_parse_dns_address_response(data, tx_id=tx_id, qtype=qtype))
    except (IndexError, OSError, UnicodeError, ValueError, struct.error) as exc:
        l.logger.debug("Ignored recoverable exception in _dns_resolve_via: %s", exc)
    return None


def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    """socket.getaddrinfo override — resolve host via custom DNS or DoH."""
    l = legacy()
    if getattr(l._dns_bypass_local, "enabled", False):
        return l._orig_getaddrinfo(host, port, family, type, proto, flags)
    if not isinstance(host, str) or not host:
        return l._orig_getaddrinfo(host, port, family, type, proto, flags)
    try:
        import ipaddress
        ipaddress.ip_address(host.strip("[]"))
        return l._orig_getaddrinfo(host, port, family, type, proto, flags)
    except ValueError as exc:
        l.logger.debug("Ignored recoverable exception in _patched_getaddrinfo: %s", exc)
    if host.lower().rstrip(".") == "localhost":
        return l._orig_getaddrinfo(host, port, family, type, proto, flags)
    if not state._active_dns or state._dns_protocol == "system":
        return l._orig_getaddrinfo(host, port, family, type, proto, flags)
    addresses = []
    try:
        qtypes = []
        if family in (0, l._socket.AF_UNSPEC, l._socket.AF_INET):
            qtypes.append(1)
        if state._dns_ipv6 and family in (
            0, l._socket.AF_UNSPEC, getattr(l._socket, "AF_INET6", -1),
        ):
            qtypes.append(28)
        for qtype in qtypes:
            ip = _dns_resolve_via(
                host, state._active_dns, qtype=qtype,
                protocol=state._dns_protocol, port=state._dns_port,
                timeout=state._dns_timeout,
            )
            if ip:
                l.logger.debug(
                    "DNS [%s/%s] %s → %s",
                    state._dns_protocol, state._active_dns, host, ip,
                )
                ip_family = l._socket.AF_INET6 if qtype == 28 else l._socket.AF_INET
                requested_family = family if family not in (0, l._socket.AF_UNSPEC) else ip_family
                addresses.extend(l._orig_getaddrinfo(
                    ip, port, requested_family, type, proto, flags,
                ))
    except (OSError, RuntimeError, TypeError, ValueError) as e:
        l.logger.debug(f"Custom DNS failed for {host}: {e}")
    if addresses:
        return addresses
    if state._dns_fallback_system:
        return l._orig_getaddrinfo(host, port, family, type, proto, flags)
    raise l._socket.gaierror(
        getattr(l._socket, "EAI_NONAME", -2),
        f"Custom DNS resolution failed for {host}",
    )


def _set_active_dns(
    server: str | None,
    *,
    protocol: str | None = None,
    port: int | None = None,
    timeout: int | None = None,
    fallback_system: bool | None = None,
    ipv6: bool | None = None,
) -> None:
    """Set global DNS server and patch/restore socket.getaddrinfo idempotently."""
    l = legacy()
    server = (server or "").strip()
    transport = _infer_dns_protocol(server, protocol)
    _validate_dns_configuration(server, transport)
    new_dns = server or None
    if transport == "system":
        new_dns = None
    new_port = max(0, min(65535, int(port if port is not None else state._dns_port)))
    new_timeout = max(1, min(60, int(timeout if timeout is not None else state._dns_timeout)))
    new_fallback = state._dns_fallback_system if fallback_system is None else bool(fallback_system)
    new_ipv6 = state._dns_ipv6 if ipv6 is None else bool(ipv6)
    desired_getaddrinfo = _patched_getaddrinfo if new_dns else l._orig_getaddrinfo
    unchanged = (
        new_dns == state._active_dns and transport == state._dns_protocol
        and new_port == state._dns_port and new_timeout == state._dns_timeout
        and new_fallback == state._dns_fallback_system and new_ipv6 == state._dns_ipv6
    )
    if unchanged:
        if l._socket.getaddrinfo is not desired_getaddrinfo:
            l._socket.getaddrinfo = desired_getaddrinfo
        return

    state._active_dns = new_dns
    state._dns_protocol = transport
    state._dns_port = new_port
    state._dns_timeout = new_timeout
    state._dns_fallback_system = new_fallback
    state._dns_ipv6 = new_ipv6
    state._dns_cache.clear()
    for name in (
        "_active_dns", "_dns_protocol", "_dns_port", "_dns_timeout",
        "_dns_fallback_system", "_dns_ipv6",
    ):
        mirror_to_legacy(name, getattr(state, name))
    if state._active_dns:
        l._socket.getaddrinfo = _patched_getaddrinfo
        l.logger.info(
            "Custom DNS active: protocol=%s endpoint=%s port=%s fallback=%s ipv6=%s",
            transport, state._active_dns,
            new_port or (853 if transport == "dot" else 53 if transport in {"udp", "tcp"} else "default"),
            "system" if new_fallback else "blocked", "on" if new_ipv6 else "off",
        )
    else:
        l._socket.getaddrinfo = l._orig_getaddrinfo
        l.logger.info("DNS restored to system default")
    l._v465_reset_shared_sessions()


def _get_active_dns() -> str | None:
    return state._active_dns


def _get_active_dns_config() -> dict[str, Any]:
    return {
        "server": state._active_dns or "",
        "protocol": state._dns_protocol,
        "port": state._dns_port,
        "timeout": state._dns_timeout,
        "fallback_system": state._dns_fallback_system,
        "ipv6": state._dns_ipv6,
    }


__all__ = [
    "_build_dns_query_wire",
    "_dns_resolve_via",
    "_doh_resolve_via",
    "_get_active_dns",
    "_get_active_dns_config",
    "_infer_dns_protocol",
    "_parse_dns_address_response",
    "_patched_getaddrinfo",
    "_resolve_dot_bootstrap",
    "_set_active_dns",
    "_validate_dns_configuration",
]
