"""Logs, metrics and tracing (blueprint 13.1: observability from day one).

- JSON logs with a request id that follows the request.
- Prometheus metrics at /metrics.
- Sentry and OpenTelemetry switch on when their settings are present and packages installed.
Logs never contain secrets, full account numbers or document contents (App. E.3).
"""

import contextvars
import json
import logging
import sys
import time
import uuid

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from app.core.config import Settings

request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

CLAIM_OUTCOMES = Counter("pops_claim_outcomes_total", "Pipeline outcomes", ["state"])
PIPELINE_SECONDS = Histogram("pops_pipeline_seconds", "Pipeline duration per claim")
PIPELINE_FAILURES = Counter("pops_pipeline_failures_total", "Pipeline exceptions")
READER_FAILURES = Counter("pops_reader_failures_total", "Reader errors", ["reader"])
GUARDRAIL_HITS = Counter("pops_guardrail_hits_total", "Guardrail rule hits", ["effect"])
UPLOADS_REJECTED = Counter("pops_uploads_rejected_total", "Rejected uploads", ["reason"])
STUCK_CLAIMS = Gauge("pops_stuck_claims", "Claims stuck in RECEIVED/PROCESSING")
OUTBOX_PENDING = Gauge("pops_outbox_pending", "Undelivered Cognify events")
HTTP_SECONDS = Histogram("pops_http_seconds", "HTTP latency", ["method", "route", "status"])


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id.get(),
        }
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out)


def configure_logging(s: Settings) -> None:
    handler = logging.StreamHandler(sys.stdout)
    if s.log_json:
        handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


def init_optional(s: Settings, app: FastAPI | None = None) -> None:
    if s.sentry_dsn:
        try:
            import sentry_sdk

            sentry_sdk.init(
                dsn=s.sentry_dsn, environment=s.env, send_default_pii=False, traces_sample_rate=0.05
            )
        except ImportError:
            logging.getLogger(__name__).warning("POPS_SENTRY_DSN set but sentry-sdk not installed")
    if s.otel_endpoint and app is not None:
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider = TracerProvider(resource=Resource.create({"service.name": "pops-engine"}))
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{s.otel_endpoint}/v1/traces"))
            )
            trace.set_tracer_provider(provider)
            FastAPIInstrumentor.instrument_app(app)
        except ImportError:
            logging.getLogger(__name__).warning("POPS_OTEL_ENDPOINT set but OTel not installed")


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def _request_context(request: Request, call_next):
        rid = request.headers.get("X-Request-Id") or uuid.uuid4().hex
        token = request_id.set(rid[:64])
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id.reset(token)
        route = request.scope.get("route")
        HTTP_SECONDS.labels(
            request.method, getattr(route, "path", "unmatched"), str(response.status_code)
        ).observe(time.perf_counter() - start)
        response.headers["X-Request-Id"] = rid[:64]
        return response

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
