# Template Changelog

This changelog tracks all changes to the template that affect downstream apps. Each entry includes migration instructions for updating apps from the template.

See `CLAUDE.md` for instructions on how to use this changelog when updating apps.

---

<!-- Add new entries at the top, below this line -->

## 2026-01-29

### Added change workflow documentation and agent configurations

**What changed:**
- Added `docs/change_workflow.md` documenting the complete workflow for making changes to the template project
- Updated `.claude/agents/` with four agent configurations adapted for template development:
  - `plan-writer.md` — Creates implementation plans for template changes
  - `plan-reviewer.md` — Reviews plans for template-specific concerns
  - `code-writer.md` — Implements plans with template awareness
  - `code-reviewer.md` — Reviews code changes for template correctness

All agents are configured to understand the unique aspects of template development: changes go to `template/`, test-app must be regenerated, tests are in `tests/`, and changelog updates are required.

**Migration steps:**
None required. This is documentation and tooling configuration only.

---

### Metrics redesign: Services own their own Prometheus metrics

**What changed:** Refactored the metrics architecture so that each service owns its own Prometheus Counter/Gauge/Histogram objects directly, rather than going through a centralized MetricsService. This eliminates potential circular dependencies in the DI container and simplifies the overall design.

Key changes:
- `MetricsService` is now minimal - only owns shutdown metrics (`application_shutting_down`, `graceful_shutdown_duration_seconds`) and provides `get_metrics_text()`
- `ConnectionManager` now owns SSE Gateway metrics directly (`sse_gateway_connections_total`, `sse_gateway_active_connections`, `sse_gateway_events_sent_total`, `sse_gateway_send_duration_seconds`)
- `TaskService` now owns task execution metrics directly (`task_execution_total`, `task_execution_duration_seconds`)
- `MetricsUpdateCoordinator` added for services that need periodic gauge updates
- Removed `MetricsServiceProtocol` and `record_task_execution()` from MetricsService
- Container no longer passes `metrics_service` to `ConnectionManager` or `TaskService`

**Migration steps:**

1. **Update MetricsService usage:**
   - Remove any calls to `metrics_service.record_task_execution()` - this is now handled internally by TaskService
   - Remove any references to `MetricsServiceProtocol`
   - If you have custom services using `metrics_service` for recording metrics, refactor them to own their metrics directly:
     ```python
     # Before
     class MyService:
         def __init__(self, metrics_service):
             self.metrics_service = metrics_service
         def do_work(self):
             self.metrics_service.record_something(...)

     # After
     from prometheus_client import Counter
     class MyService:
         def __init__(self):
             self.my_counter = Counter('my_counter', 'Description', ['label'])
         def do_work(self):
             self.my_counter.labels(label='value').inc()
     ```

2. **Update container.py:**
   - Remove `metrics_service` parameter from `ConnectionManager` provider
   - Remove `metrics_service` parameter from `TaskService` provider
   - Example diff:
     ```python
     # Before
     connection_manager = providers.Singleton(
         ConnectionManager,
         gateway_url=config.provided.SSE_GATEWAY_URL,
         http_timeout=2.0,
         metrics_service=metrics_service,
     )

     # After
     connection_manager = providers.Singleton(
         ConnectionManager,
         gateway_url=config.provided.SSE_GATEWAY_URL,
         http_timeout=2.0,
     )
     ```

3. **For periodic gauge updates:**
   - If you have services with gauge metrics that need periodic refresh, use `MetricsUpdateCoordinator`:
     ```python
     # In app factory
     coordinator = container.metrics_coordinator()
     coordinator.register_updater(my_service.update_metrics)
     coordinator.start(interval_seconds=60)
     ```

4. **Copy updated files from template:**
   - `common/metrics/service.py`
   - `common/metrics/coordinator.py`
   - `common/metrics/__init__.py`
   - `common/sse/connection_manager.py`
   - `common/tasks/service.py`
   - `common/core/container.py` (regenerate from template or manually update)
