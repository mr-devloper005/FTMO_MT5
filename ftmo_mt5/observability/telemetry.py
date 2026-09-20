"""OpenTelemetry instrumentation and metrics setup for FTMO MT5 trading system.

Provides centralized telemetry configuration including distributed tracing
and metrics collection using industry-standard instrumentation.
"""

__all__ = [
    "setup_telemetry",
    "get_tracer",
    "get_meter",
    "traced",
]

from functools import wraps
from typing import Any, Callable, Optional, Tuple

try:
    from opentelemetry import trace, metrics
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.exporter.prometheus import PrometheusMetricReader
    from opentelemetry.sdk.resources import Resource
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    # Provide dummy implementations for when OpenTelemetry is not available
    trace = None
    metrics = None
    TracerProvider = None
    SimpleSpanProcessor = None
    OTLPSpanExporter = None
    OTLPMetricExporter = None
    MeterProvider = None
    PeriodicExportingMetricReader = None
    PrometheusMetricReader = None
    Resource = None


# Module-level tracer and meter instances
_tracer: Optional[trace.Tracer] = None
_meter: Optional[metrics.Meter] = None


def setup_telemetry(
    service_name: str = "ftmo-mt5",
    otel_endpoint: Optional[str] = None,
) -> Tuple[Optional[Any], Optional[Any]]:
    """Configure OpenTelemetry tracing and metrics.

    Sets up TracerProvider with OTLP gRPC exporter (or console fallback) and
    MeterProvider with Prometheus exporter at 5-second intervals.

    If OpenTelemetry is not available, returns (None, None) and prints a warning.

    Args:
        service_name: Name of the service for telemetry attribution.
        otel_endpoint: OTLP gRPC endpoint (e.g., "localhost:4317").
                      If None, uses console exporter fallback.

    Returns:
        Tuple of (tracer, meter) for instrumentation, or (None, None) if OTel unavailable.
    """
    global _tracer, _meter

    if not OTEL_AVAILABLE:
        print("WARNING: OpenTelemetry not installed. Telemetry disabled. Install with: pip install opentelemetry-api opentelemetry-sdk")
        _tracer = None
        _meter = None
        return None, None

    # Create resource with service name
    resource = Resource.create({
        "service.name": service_name,
        "service.version": "1.0.0",
    })

    # ========================================================================
    # Tracing Setup
    # ========================================================================
    if otel_endpoint:
        # OTLP gRPC exporter
        trace_exporter = OTLPSpanExporter(endpoint=otel_endpoint)
    else:
        # Fallback to simple in-memory exporter
        try:
            from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
                InMemorySpanExporter,
            )
            trace_exporter = InMemorySpanExporter()
        except ImportError:
            # Final fallback: use SimpleSpanProcessor with stdout
            from opentelemetry.sdk.trace.export import SimpleSpanProcessor as _SimpleSpanProcessor
            trace_exporter = None

    tracer_provider = TracerProvider(resource=resource)
    if trace_exporter:
        tracer_provider.add_span_processor(SimpleSpanProcessor(trace_exporter))
    trace.set_tracer_provider(tracer_provider)

    _tracer = trace.get_tracer(__name__)

    # ========================================================================
    # Metrics Setup
    # ========================================================================
    # Prometheus exporter for metrics
    try:
        prometheus_reader = PrometheusMetricReader()
    except Exception:
        # If Prometheus reader fails, use minimal reader
        prometheus_reader = None

    # If OTLP endpoint provided, add OTLP exporter as well
    if otel_endpoint:
        try:
            metric_exporter = OTLPMetricExporter(endpoint=otel_endpoint)
            otlp_reader = PeriodicExportingMetricReader(
                metric_exporter,
                interval_millis=5000,  # 5-second intervals
            )
            if prometheus_reader:
                meter_provider = MeterProvider(
                    resource=resource,
                    metric_readers=[prometheus_reader, otlp_reader],
                )
            else:
                meter_provider = MeterProvider(
                    resource=resource,
                    metric_readers=[otlp_reader],
                )
        except Exception:
            if prometheus_reader:
                meter_provider = MeterProvider(
                    resource=resource,
                    metric_readers=[prometheus_reader],
                )
            else:
                meter_provider = MeterProvider(resource=resource)
    else:
        if prometheus_reader:
            meter_provider = MeterProvider(
                resource=resource,
                metric_readers=[prometheus_reader],
            )
        else:
            meter_provider = MeterProvider(resource=resource)

    metrics.set_meter_provider(meter_provider)
    _meter = metrics.get_meter(__name__)

    # ========================================================================
    # Create Standard Metrics
    # ========================================================================
    # Trade latency histogram (10ms to 10s in exponential buckets)
    _meter.create_histogram(
        name="trade_latency_ms",
        description="Trade execution latency in milliseconds",
        unit="ms",
    )

    # Order execution counters
    _meter.create_counter(
        name="order_success_total",
        description="Total successful order executions",
    )
    _meter.create_counter(
        name="order_failure_total",
        description="Total failed order executions",
    )

    # Risk state gauge (labeled by state)
    _meter.create_gauge(
        name="risk_state",
        description="Current system risk state (1.0 if active, 0.0 otherwise)",
    )

    # Account metrics gauges
    _meter.create_gauge(
        name="equity_usd",
        description="Current account equity in USD",
        unit="USD",
    )
    _meter.create_gauge(
        name="mdl_headroom_pct",
        description="Maximum Daily Loss headroom as percentage of limit",
        unit="%",
    )
    _meter.create_gauge(
        name="ml_headroom_pct",
        description="Maximum Loss headroom as percentage of limit",
        unit="%",
    )

    # Position tracking gauge
    _meter.create_gauge(
        name="position_count",
        description="Number of open positions",
    )

    # System health gauges
    _meter.create_gauge(
        name="reconciliation_health",
        description="Reconciliation health status (1.0=healthy, 0.0=unhealthy)",
    )
    _meter.create_gauge(
        name="agent_heartbeat",
        description="Agent heartbeat status (1.0=alive, 0.0=dead)",
    )
    _meter.create_gauge(
        name="event_store_size",
        description="Number of events in event store",
    )

    # ML drift monitoring gauge
    _meter.create_gauge(
        name="psi_drift_score",
        description="Population Stability Index (PSI) drift score",
    )

    return _tracer, _meter


def get_tracer() -> trace.Tracer:
    """Get the module-level tracer instance.

    Returns:
        OpenTelemetry Tracer for creating spans.

    Raises:
        RuntimeError: If setup_telemetry() has not been called.
    """
    if _tracer is None:
        raise RuntimeError("Telemetry not initialized. Call setup_telemetry() first.")
    return _tracer


def get_meter() -> metrics.Meter:
    """Get the module-level meter instance.

    Returns:
        OpenTelemetry Meter for recording metrics.

    Raises:
        RuntimeError: If setup_telemetry() has not been called.
    """
    if _meter is None:
        raise RuntimeError("Telemetry not initialized. Call setup_telemetry() first.")
    return _meter


def traced(name: Optional[str] = None) -> Callable:
    """Decorator for easy method instrumentation with distributed tracing.

    Creates a span for the decorated function and automatically records
    execution time and exceptions.

    Args:
        name: Optional custom span name. If None, uses function name.

    Returns:
        Decorator function.

    Example:
        @traced(name="execute_trade")
        async def execute_trade(self, request: TradeRequest) -> ExecutionResult:
            ...
    """

    def decorator(func: Callable) -> Callable:
        span_name = name or func.__name__

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            # Handle case where telemetry is not initialized
            if not OTEL_AVAILABLE:
                return await func(*args, **kwargs)

            try:
                tracer = get_tracer()
                if tracer is None:
                    return await func(*args, **kwargs)

                with tracer.start_as_current_span(span_name) as span:
                    try:
                        result = await func(*args, **kwargs)
                        span.set_attribute("status", "success")
                        return result
                    except Exception as e:
                        span.set_attribute("status", "error")
                        span.set_attribute("error.type", type(e).__name__)
                        span.set_attribute("error.message", str(e))
                        raise
            except RuntimeError:
                # Tracer not initialized, run function without tracing
                return await func(*args, **kwargs)

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            # Handle case where telemetry is not initialized
            if not OTEL_AVAILABLE:
                return func(*args, **kwargs)

            try:
                tracer = get_tracer()
                if tracer is None:
                    return func(*args, **kwargs)

                with tracer.start_as_current_span(span_name) as span:
                    try:
                        result = func(*args, **kwargs)
                        span.set_attribute("status", "success")
                        return result
                    except Exception as e:
                        span.set_attribute("status", "error")
                        span.set_attribute("error.type", type(e).__name__)
                        span.set_attribute("error.message", str(e))
                        raise
            except RuntimeError:
                # Tracer not initialized, run function without tracing
                return func(*args, **kwargs)

        # Return appropriate wrapper based on function type
        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator
