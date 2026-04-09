---
description: Retrieve geolocation and network information for the server's current IPv4 and IPv6 addresses via ipinfo.io
---

# IP Lookup

Fetch network and geolocation metadata for the MCP server's public IP addresses.

## Prerequisites

- No parameters required — lookups are for the server's own outbound IP
- Outbound internet access to `ipinfo.io` must be available

## Workflow

1. **IPv4 lookup** — call `get_ipv4_info` to get the server's public IPv4 address and associated metadata
2. **IPv6 lookup** — call `get_ipv6_info` to get the server's public IPv6 address (if available)

## Tool Parameters

Both tools take no parameters.

### `get_ipv4_info`
Returns: JSON object from `https://ipinfo.io/json`

### `get_ipv6_info`
Returns: JSON object from `https://v6.ipinfo.io/json`

> **Note**: `get_ipv6_info` temporarily monkey-patches the socket family to `AF_INET6` to force an IPv6 connection. This is reverted automatically after the call. Do not call this concurrently with other network operations.

## Response Fields

| Field | Description | Example |
|---|---|---|
| `ip` | Public IP address | `"203.0.113.1"` |
| `hostname` | Reverse DNS hostname | `"host.example.com"` |
| `city` | City of the IP | `"London"` |
| `region` | Region/state | `"England"` |
| `country` | ISO 3166-1 alpha-2 country code | `"GB"` |
| `loc` | Latitude,Longitude | `"51.5074,-0.1278"` |
| `org` | ASN and organisation name | `"AS12345 Example ISP"` |
| `postal` | Postal/ZIP code | `"EC1A"` |
| `timezone` | IANA timezone | `"Europe/London"` |

## Best Practices

- Use `get_ipv4_info` for standard connectivity diagnostics — it is always available
- Use `get_ipv6_info` only when IPv6 connectivity is specifically needed; it may fail if the network does not support IPv6
- If `get_ipv6_info` fails with a connection error, the server's network does not have IPv6 egress — this is expected in many environments
- Do not use these tools to geolocate arbitrary external IPs — they only report the server's own addresses
