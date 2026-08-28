# Metrics

In HTTP mode the server exposes Prometheus metrics at `GET /metrics`, on the same port as `/mcp` (`PORT`, default `8081`). The endpoint is unauthenticated so it can be scraped directly.

```sh
curl http://localhost:8081/metrics
```

| Metric | Type | Description |
|--------|------|-------------|
| `mcp_tool_invocations_total` | counter | Tool calls that completed, labelled by `tool_name` |
| `mcp_tool_duration_seconds` | histogram | Tool call duration in seconds, labelled by `tool_name` |
| `mcp_tool_in_progress` | gauge | Tool calls currently running |
| `process_*` | gauge | CPU, memory and file descriptor usage of the server process, Linux only |
| `python_info` | gauge | Interpreter version |

Only completed calls are counted, which keeps the `tool_name` label bounded to registered tools. A call that raises is recorded in neither the counter nor the histogram, but it is still reflected in `mcp_tool_in_progress` while it runs.

## Scraping

The Kubernetes manifests under `deployment/` set `prometheus.io/scrape: "true"` and `prometheus.io/port: "8081"` on the pod, so a Prometheus instance using pod annotations picks the endpoint up with no further configuration.

For a static scrape config:

```yaml
scrape_configs:
  - job_name: mcp-github
    static_configs:
      - targets: ['mcp-github:8081']
```

## Useful queries

```promql
# call rate per tool
sum by (tool_name) (rate(mcp_tool_invocations_total[5m]))

# 95th percentile duration per tool
histogram_quantile(0.95, sum by (le, tool_name) (rate(mcp_tool_duration_seconds_bucket[5m])))

# calls started but never completed, a proxy for errors and timeouts
mcp_tool_in_progress
```
