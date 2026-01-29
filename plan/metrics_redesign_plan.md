# Metrics Redesign Plan

## Problem Statement

The current MetricsService design has metrics defined centrally but updated by reaching into other services. This creates:

1. **Circular dependencies**: MetricsService needs DashboardService to pull data, but domain services need MetricsService to record events
2. **Complex DI workarounds**: Required `providers.Self()` or factory injection patterns
3. **Unclear ownership**: Metrics are defined in one place but semantically belong to domain services
4. **Inheritance blockers**: Container inheritance becomes complex due to circular dependencies

## Key Insight

Prometheus metrics (Counter, Gauge, Histogram) register themselves with a global `CollectorRegistry`. They don't need a central MetricsService to own them. Each service can own and update its own metrics directly.

## Target Design

### MetricsService (Infrastructure Only)

MetricsService becomes thin, handling only cross-cutting infrastructure concerns:

```python
class MetricsService:
    """Infrastructure metrics only. Domain services own their own metrics."""

    def __init__(self, shutdown_coordinator: ShutdownCoordinatorProtocol):
        self.shutdown_coordinator = shutdown_coordinator
        self._init_infrastructure_metrics()
        shutdown_coordinator.register_lifetime_notification(self._on_lifetime_event)

    def _init_infrastructure_metrics(self):
        # Shutdown metrics
        self.application_shutting_down = Gauge(
            'application_shutting_down',
            'Whether application is shutting down (1=yes, 0=no)'
        )
        self.graceful_shutdown_duration_seconds = Histogram(
            'graceful_shutdown_duration_seconds',
            'Duration of graceful shutdowns'
        )

        # SSE Gateway metrics
        self.sse_gateway_connections_total = Counter(
            'sse_gateway_connections_total',
            'Total SSE Gateway connection lifecycle events',
            ['action']
        )
        self.sse_gateway_events_sent_total = Counter(
            'sse_gateway_events_sent_total',
            'Total events sent to SSE Gateway',
            ['service', 'status']
        )
        self.sse_gateway_send_duration_seconds = Histogram(
            'sse_gateway_send_duration_seconds',
            'Duration of SSE Gateway HTTP send calls',
            ['service']
        )
        self.sse_gateway_active_connections = Gauge(
            'sse_gateway_active_connections',
            'Current number of active SSE Gateway connections'
        )

    def record_sse_gateway_connection(self, action: str) -> None:
        self.sse_gateway_connections_total.labels(action=action).inc()
        if action == "connect":
            self.sse_gateway_active_connections.inc()
        elif action == "disconnect":
            self.sse_gateway_active_connections.dec()

    # ... other SSE and shutdown methods

    def get_metrics_text(self) -> str:
        """All metrics from global registry, regardless of who owns them."""
        return generate_latest().decode('utf-8')
```

### Domain Services Own Their Metrics

Each domain service defines and updates its own metrics:

```python
class DashboardService:
    """Dashboard queries and inventory metrics."""

    def __init__(self, db: Session):
        self.db = db
        self._init_metrics()

    def _init_metrics(self):
        self.inventory_total_parts = Gauge(
            'inventory_total_parts', 'Total parts in system'
        )
        self.inventory_total_quantity = Gauge(
            'inventory_total_quantity', 'Sum of all quantities'
        )
        self.inventory_low_stock_parts = Gauge(
            'inventory_low_stock_parts', 'Parts with qty <= 5'
        )
        # ... etc

    def update_metrics(self) -> None:
        """Update gauge metrics with current values."""
        stats = self.get_dashboard_stats()
        self.inventory_total_parts.set(stats['total_parts'])
        self.inventory_total_quantity.set(stats['total_quantity'])
        self.inventory_low_stock_parts.set(stats['low_stock_count'])


class KitService:
    """Kit management and kit metrics."""

    def __init__(self, db: Session, ...):
        self.db = db
        self._init_metrics()

    def _init_metrics(self):
        self.kits_created_total = Counter('kits_created_total', 'Total kits created')
        self.kits_archived_total = Counter('kits_archived_total', 'Total kits archived')
        # ... etc

    def create_kit(self, ...) -> Kit:
        kit = Kit(...)
        self.db.add(kit)
        self.kits_created_total.inc()  # Update metric inline
        return kit
```

### Background Metric Updates

For gauge metrics that need periodic refresh, two options:

**Option A: Coordinator pattern (recommended)**

A lightweight coordinator calls registered updaters:

```python
class MetricsUpdateCoordinator:
    """Coordinates periodic metric updates across services."""

    def __init__(self, shutdown_coordinator: ShutdownCoordinatorProtocol):
        self._updaters: list[Callable[[], None]] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        shutdown_coordinator.register_lifetime_notification(self._on_shutdown)

    def register_updater(self, updater: Callable[[], None]) -> None:
        """Register a service's update_metrics method."""
        self._updaters.append(updater)

    def start(self, interval_seconds: int = 60) -> None:
        self._thread = threading.Thread(target=self._update_loop, args=(interval_seconds,), daemon=True)
        self._thread.start()

    def _update_loop(self, interval: int) -> None:
        while not self._stop_event.wait(interval):
            for updater in self._updaters:
                try:
                    updater()
                except Exception as e:
                    logger.error(f"Metric update failed: {e}")
```

**Option B: Services manage their own refresh**

Each service that needs background updates runs its own timer. Simpler but less coordinated.

**Recommendation**: Option A - single coordinator, services register their `update_metrics` method.

---

## Implementation Tasks

### Task 1: Create Template Infrastructure

**Scope**: ModernAppTemplate

Create the common metrics infrastructure in the template.

**Files to create**:
- `template/common/metrics/__init__.py`
- `template/common/metrics/service.py` - Infrastructure MetricsService
- `template/common/metrics/coordinator.py` - MetricsUpdateCoordinator

**MetricsService** (infrastructure only):
- SSE Gateway metrics
- Shutdown metrics
- `get_metrics_text()` method

**MetricsUpdateCoordinator**:
- Register/unregister updaters
- Background update loop
- Graceful shutdown integration

### Task 2: Update Template Container

**Scope**: ModernAppTemplate

Update CommonContainer with the new metrics components.

**File**: `template/common/container.py`

```python
class CommonContainer(containers.DeclarativeContainer):
    config = providers.Dependency()
    session_maker = providers.Dependency()

    db_session = providers.ContextLocalSingleton(session_maker.provided.call())

    shutdown_coordinator = providers.Singleton(
        ShutdownCoordinator,
        graceful_shutdown_timeout=config.provided.GRACEFUL_SHUTDOWN_TIMEOUT,
    )

    # Simple - no factory injection needed!
    metrics_service = providers.Singleton(
        MetricsService,
        shutdown_coordinator=shutdown_coordinator,
    )

    metrics_coordinator = providers.Singleton(
        MetricsUpdateCoordinator,
        shutdown_coordinator=shutdown_coordinator,
    )

    connection_manager = providers.Singleton(
        ConnectionManager,
        gateway_url=config.provided.SSE_GATEWAY_URL,
        metrics_service=metrics_service,
        http_timeout=2.0,
    )

    # ... etc
```

No circular dependencies. No factory injection. Clean and simple.

### Task 3: Migrate ElectronicsInventory MetricsService

**Scope**: ElectronicsInventory

Split the current monolithic MetricsService.

**Changes**:

1. **Keep in MetricsService** (infrastructure):
   - SSE Gateway metrics
   - Shutdown metrics
   - `get_metrics_text()`

2. **Move to DashboardService**:
   - `inventory_total_parts`
   - `inventory_total_quantity`
   - `inventory_low_stock_parts`
   - `inventory_parts_without_docs`
   - `inventory_box_utilization_percent`
   - `inventory_total_boxes`
   - `inventory_recent_changes_7d`
   - `inventory_recent_changes_30d`
   - `inventory_parts_by_type`
   - `update_metrics()` method

3. **Move to KitService**:
   - `kits_created_total`
   - `kits_archived_total`
   - `kits_unarchived_total`
   - `kits_overview_requests_total`
   - `kits_active_count`
   - `kits_archived_count`
   - `kit_detail_views_total`
   - `kit_content_mutations_total`
   - `kit_content_update_duration_seconds`
   - `kit_shopping_list_push_total`
   - `kit_shopping_list_push_seconds`
   - `kit_shopping_list_unlink_total`

4. **Move to PickListService** (or KitPickListService):
   - `pick_list_created_total`
   - `pick_list_lines_per_creation`
   - `pick_list_line_picked_total`
   - `pick_list_line_undo_total`
   - `pick_list_line_undo_duration_seconds`
   - All other pick list metrics

5. **Move to ShoppingListService** (or ShoppingListLineService):
   - `shopping_list_lines_marked_ordered_total`
   - `shopping_list_lines_received_total`
   - `shopping_list_receive_quantity_total`

6. **Move to AIService**:
   - `ai_analysis_requests_total`
   - `ai_analysis_duration_seconds`
   - `ai_analysis_tokens_total`
   - `ai_analysis_cost_dollars_total`
   - `ai_duplicate_search_*` metrics

7. **Move to InventoryService**:
   - `inventory_quantity_changes_total`

### Task 4: Update Service Method Calls

**Scope**: ElectronicsInventory

Replace `metrics_service.record_X()` calls with direct metric updates.

**Before**:
```python
class KitService:
    def __init__(self, db, metrics_service, ...):
        self.metrics_service = metrics_service

    def create_kit(self, ...):
        kit = Kit(...)
        self.metrics_service.record_kit_created()
        return kit
```

**After**:
```python
class KitService:
    def __init__(self, db, ...):
        self.db = db
        self.kits_created_total = Counter('kits_created_total', 'Total kits created')

    def create_kit(self, ...):
        kit = Kit(...)
        self.kits_created_total.inc()
        return kit
```

**Note**: Services no longer need `metrics_service` injected for recording domain events.

### Task 5: Register Background Updaters

**Scope**: ElectronicsInventory

Wire up services that need background metric updates.

**File**: `app/__init__.py`

```python
def create_app(settings, skip_background_services=False):
    # ... app setup ...

    if not skip_background_services:
        # Get services that have metrics to update
        dashboard_service = container.dashboard_service()

        # Register updaters with coordinator
        metrics_coordinator = container.metrics_coordinator()
        metrics_coordinator.register_updater(dashboard_service.update_metrics)
        # Add other services with gauge metrics as needed

        # Start background updates
        metrics_coordinator.start(settings.METRICS_UPDATE_INTERVAL)
```

### Task 6: Simplify AppContainer

**Scope**: ElectronicsInventory

Remove factory injection from container now that MetricsService is simple.

**Before**:
```python
metrics_service = providers.Singleton(
    MetricsService,
    shutdown_coordinator=shutdown_coordinator,
    db_session_factory=providers.Object(db_session),
    dashboard_service_factory=providers.Object(dashboard_service),
    db_session_reset=db_session.reset,
)
```

**After**:
```python
metrics_service = providers.Singleton(
    MetricsService,
    shutdown_coordinator=shutdown_coordinator,
)
```

### Task 7: Enable Container Inheritance

**Scope**: ElectronicsInventory

With the simplified design, AppContainer can now extend CommonContainer cleanly.

```python
from common.container import CommonContainer

class AppContainer(CommonContainer):
    """ElectronicsInventory application container."""

    config = providers.Dependency(instance_of=Settings)

    # Domain services only - infrastructure comes from CommonContainer
    dashboard_service = providers.Factory(
        DashboardService,
        db=CommonContainer.db_session
    )
    kit_service = providers.Factory(
        KitService,
        db=CommonContainer.db_session,
        # Note: no metrics_service needed!
    )
    # ... etc
```

### Task 8: Update Tests

**Scope**: ElectronicsInventory

Update test files to reflect the new design.

**Changes**:
- `test_metrics_service.py` - Test only infrastructure metrics
- Domain service tests - Verify metrics are initialized and updated
- Remove metrics_service mocking from services that no longer use it
- Update test helper `get_real_metrics_service()` if still needed

### Task 9: Update Template Documentation

**Scope**: ModernAppTemplate

Document the metrics pattern for app developers.

**Topics**:
- How to add domain metrics to a service
- How to register for background updates
- What MetricsService provides vs what services own
- Container inheritance patterns

---

## Migration Strategy

### Phase 1: Template Infrastructure (Tasks 1-2)
- Create new metrics module in template
- No breaking changes to existing apps

### Phase 2: EI Migration (Tasks 3-6)
- Migrate one service at a time
- Run tests after each service
- Keep old MetricsService methods as deprecated pass-throughs during migration

### Phase 3: Cleanup (Tasks 7-8)
- Enable container inheritance
- Remove deprecated code
- Update tests

### Phase 4: Documentation (Task 9)
- Document patterns
- Update template README

---

## Verification Checklist

- [ ] All 1144 EI tests pass
- [ ] MetricsService has no domain-specific metrics
- [ ] MetricsService has no db_session or dashboard_service dependencies
- [ ] Domain services own their metrics
- [ ] Background gauge updates work via coordinator
- [ ] `/metrics` endpoint returns all metrics (from global registry)
- [ ] Container inheritance works (AppContainer extends CommonContainer)
- [ ] No `providers.Self()`, `providers.Object()`, or `providers.Delegate()` workarounds

---

## Benefits Summary

| Before | After |
|--------|-------|
| MetricsService owns all metrics | Services own their metrics |
| Complex factory injection | Simple constructor injection |
| Circular dependencies | No circular dependencies |
| `providers.Self()` workaround | Standard DI patterns |
| Container inheritance blocked | Clean inheritance possible |
| Unclear metric ownership | Clear ownership by domain |
| Monolithic 1100-line MetricsService | Thin infrastructure service |
