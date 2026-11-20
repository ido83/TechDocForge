notes on the new report-cache service

we added a cache in front of the reporting API because dashboards were slow (p95 was ~4s).
it's a small python service using redis 7. it sits between the web app and reporting-api.
reports are cached for 15 minutes by default, can be changed with CACHE_TTL env var.
key is a hash of user org + report params. admins can call POST /cache/flush to clear it.
redis password comes from vault. runs on k8s, 2 replicas. grafana dashboard "report-cache".
worry: stale data right after imports, maybe flush after import job finishes?
