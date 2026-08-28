# Metrics

In HTTP mode the server exposes Prometheus metrics at `GET /metrics`, on the same port as `/mcp` (`PORT`, default `8081`). The endpoint is unauthenticated so it can be scraped directly.

```sh
curl http://localhost:8081/metrics
```

| Metric | Type | Description |
|--------|------|-------------|
| `mcp_tool_invocations_total` | counter | Tool calls, labelled by `tool_name` and `outcome` |
| `mcp_tool_duration_seconds` | histogram | Tool call duration in seconds, labelled by `tool_name` and `outcome` |
| `mcp_tool_in_progress` | gauge | Tool calls currently running |
| `process_*` | gauge | CPU, memory and file descriptor usage of the server process, Linux only |
| `python_info` | gauge | Interpreter version |

`outcome` is `success` or `error`, so both are counted and both are timed. A call for a tool that does not exist is recorded as `tool_name="unknown"`, which keeps the label bounded to registered tools whatever a client asks for.

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

# error rate as a fraction of all calls
sum(rate(mcp_tool_invocations_total{outcome="error"}[5m]))
  / sum(rate(mcp_tool_invocations_total[5m]))

# the tools that are failing
sum by (tool_name) (rate(mcp_tool_invocations_total{outcome="error"}[5m])) > 0

# 95th percentile duration of successful calls, per tool
histogram_quantile(0.95, sum by (le, tool_name) (rate(mcp_tool_duration_seconds_bucket{outcome="success"}[5m])))

# calls currently running
mcp_tool_in_progress
```
