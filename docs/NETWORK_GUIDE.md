# Network, DNS, Proxy, and VPN Guard Guide

Network settings are available under **Settings → Network**. After editing the
profile, select **Save & apply**, then **Validate offline**. Offline validation
checks formats, the effective in-process profile, and local interfaces only. It
does not request CYOA.CAFE, a favicon, a public DNS resolver, or another test
website.

The profile is stored in `~/.cyoa_downloader/settings.json` and is shared by
the main GUI, advanced Settings page, and CLI.

## Proxy routing

Available modes:

- `inherit_env` uses `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and `NO_PROXY`
  from the process environment.
- `manual` uses the common proxy and optional HTTP/HTTPS overrides entered in
  Settings.
- `disabled` ignores manual and environment proxy configuration.

Supported proxy URL schemes are `http://`, `https://`, `socks4://`,
`socks4a://`, `socks5://`, and `socks5h://`. Use `socks5h://` when the proxy
must resolve destination hostnames. The bypass field accepts comma-separated
hosts such as `localhost,127.0.0.1,::1,.local`.

Proxy URLs may contain credentials. Log messages redact usernames and
passwords, and portable settings export removes secret fields. The active
settings file may still need the original credentials at runtime and must be
protected accordingly.

Requests sessions and browser fallbacks consume the same effective profile.
HTTP and HTTPS overrides take precedence over the common proxy for their
respective schemes.

## DNS transports and presets

The **Preset** menu contains System, Cloudflare `1.1.1.1`/`1.0.0.1`, Google
`8.8.8.8`/`8.8.4.4`, Quad9 `9.9.9.9`, encrypted DoH/DoT endpoints, and
BebasDNS variants. Selecting a preset fills in the transport and endpoint.
Select **Custom…** to enter another resolver.

Older GUI builds exposed only a free-form field, so `1.1.1.1` could be entered
manually but did not appear as a selectable option.

| Protocol | Example endpoint | Default port | Notes |
| --- | --- | ---: | --- |
| `system` | empty | OS | Uses the operating-system resolver |
| `udp` | `1.1.1.1` | 53 | Plain, unencrypted DNS over UDP |
| `tcp` | `1.1.1.1` | 53 | Plain, unencrypted DNS over TCP |
| `doh` | `https://cloudflare-dns.com/dns-query` | HTTPS | DNS wire format over HTTPS |
| `dot` | `tls://one.one.one.one` | 853 | DNS over TLS with hostname verification |

Port `0` selects the transport default. Timeout applies per query. **Resolve
IPv6 / AAAA** enables IPv6 address lookup.

UDP and TCP endpoints must be IP literals. DoH requires a complete `https://`
URL. DoT requires a hostname rather than a bare IP so the TLS certificate can
be verified. The hostname is resolved with the original OS resolver only to
bootstrap the encrypted connection; it is then passed separately as the TLS
server name.

If **Fall back to system DNS** is enabled, a failed custom lookup can use the OS
resolver. This improves compatibility but may leak the query to the system DNS
provider. Disable fallback for fail-closed behavior.

DoH follows the application's proxy profile. With a SOCKS proxy, `socks5h`
keeps destination hostname resolution on the proxy side. DNS settings affect
only the CYOA Downloader process; Windows, browsers, routers, and hosts files
are not changed.

## VPN guard

CYOA Downloader does not implement a VPN protocol or manage a VPN provider.
The tunnel must already be active through WireGuard, OpenVPN, Tailscale,
NordVPN, or another operating-system VPN client.

- `system` places no VPN requirement on downloader requests and follows the OS
  route table.
- `require` blocks downloader requests unless a VPN-like active interface is
  detected.
- **Interface name contains** optionally restricts detection to names such as
  `WireGuard`, `Wintun`, or a provider-specific adapter. Matching is
  case-insensitive.

Detection is intentionally fail-closed in `require` mode: when interface state
cannot be confirmed, the request does not continue. This is an application
guard, not an operating-system kill switch. Other applications and traffic
outside the downloader pipeline remain out of scope.

The previous informational message “VPN routing follows the operating system”
only meant that the guard was in `system` mode; it did not mean that a VPN was
active. That routine status is now logged at debug level.

## CLI examples

UDP and TCP:

```powershell
python cyoa_downloader.py URL --dns 1.1.1.1 --dns-protocol udp
python cyoa_downloader.py URL --dns 1.1.1.1 --dns-protocol tcp --dns-timeout 8
```

DoH without system fallback:

```powershell
python cyoa_downloader.py URL `
  --dns https://cloudflare-dns.com/dns-query `
  --dns-protocol doh `
  --no-dns-fallback-system
```

DoT, per-scheme proxies, and a required VPN interface:

```powershell
python cyoa_downloader.py URL `
  --dns tls://one.one.one.one --dns-protocol dot --dns-port 853 `
  --proxy-mode manual `
  --proxy-http http://127.0.0.1:8080 `
  --proxy-https socks5h://127.0.0.1:1080 `
  --proxy-bypass localhost,127.0.0.1,::1 `
  --vpn-policy require --vpn-interface WireGuard
```

Use `--dns-ipv6` / `--no-dns-ipv6` and `--dns-fallback-system` /
`--no-dns-fallback-system` to set boolean options. Explicit CLI network values
are saved for the next run. Without an explicit override, the CLI uses the
profile saved by the GUI.

## CYOA.CAFE troubleshooting

A `cyoa.cafe/game/...` page is a catalog record. The downloader reads the
record and follows its actual creator `iframe_url`. Teen Titans currently
points to `laath.cyoa.cafe/teen-titans-cyoa/`; changing DNS or VPN is not
normally necessary when both hosts are reachable.

If a download fails:

1. Select **Validate offline** and check whether the VPN guard would block the
   downloader.
2. Temporarily select system DNS to distinguish a resolver configuration issue
   from a website issue.
3. Confirm that manual proxy mode does not point to a stopped local service.
4. Inspect `cyoa_downloader.log` and `backup_report.txt`. One optional favicon
   failure does not mean that project assets failed.
5. Run `python cyoa_downloader.py --dependency-check` to confirm that dnspython
   and Requests SOCKS support are available.

Settings validation itself never downloads a favicon or probes the target
website.

## Implementation references

- [Requests: proxies and SOCKS](https://requests.readthedocs.io/en/stable/user/advanced/#proxies)
- [Playwright: browser proxy configuration](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-option-proxy)
- [dnspython: UDP, TCP, and TLS queries](https://dnspython.readthedocs.io/en/stable/query.html)
- [Cloudflare: DNS over HTTPS](https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/)
- [Cloudflare: DNS over TLS](https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-tls/)
- [Google Public DNS: DoH](https://developers.google.com/speed/public-dns/docs/doh/)
- [Quad9: resolver services](https://docs.quad9.net/services/)
