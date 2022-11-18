import typing

from opentelemetry import trace
from opentelemetry.context import Context, create_key, get_current
from opentelemetry.propagators.textmap import (
    CarrierT,
    Getter,
    Setter,
    TextMapPropagator,
    default_getter,
    default_setter,
)
from opentelemetry.trace import TraceFlags, NonRecordingSpan, SpanContext

from sentry_sdk.integrations.opentelemetry.span_processor import SentrySpanProcessor
from sentry_sdk.tracing import SENTRY_TRACE_HEADER_NAME, Transaction
from sentry_sdk.tracing_utils import Baggage


BAGGAGE_HEADER_NAME = "sentry-baggage"


SENTRY_TRACE_KEY = create_key("sentry-trace")
SENTRY_BAGGAGE_KEY = create_key("sentry-baggage")


class SentryPropagator(TextMapPropagator):
    def extract(
        self,
        carrier: CarrierT,
        context: typing.Optional[Context] = None,
        getter: Getter = default_getter,
    ) -> Context:
        if context is None:
            context = get_current()

        sentry_trace = getter.get(carrier, SENTRY_TRACE_HEADER_NAME)
        if not sentry_trace:
            return context

        sentry_trace_data = Transaction.extract_sentry_trace(sentry_trace)
        context = context.set_value(SENTRY_TRACE_KEY, sentry_trace_data)

        trace_id, span_id, _parent_sampled = sentry_trace_data

        span_context = SpanContext(
            trace_id=trace_id,
            span_id=span_id,
            # we simulate a sampled trace on the otel side and leave the sampling to sentry
            trace_flags=TraceFlags.SAMPLED,
            is_remote=True,
        )

        baggage_header = getter.get(carrier, BAGGAGE_HEADER_NAME)

        if baggage_header:
            baggage = Baggage.from_incoming_header(baggage_header)
        else:
            # If there's an incoming sentry-trace but no incoming baggage header,
            # for instance in traces coming from older SDKs,
            # baggage will be empty and frozen and won't be populated as head SDK.
            baggage = Baggage()

        baggage.freeze()
        context = context.set_value(SENTRY_BAGGAGE_KEY, baggage)

        span = NonRecordingSpan(span_context)
        modified_context = trace.set_span_in_context(span, context)
        return modified_context

    def inject(
        self,
        carrier: CarrierT,
        context: typing.Optional[Context] = None,
        setter: Setter = default_setter,
    ) -> None:
        if context is None:
            context = get_current()

        current_span = trace.get_current_span(context)
        span_id = trace.format_span_id(current_span.context.span_id)

        span_map = SentrySpanProcessor().otel_span_map
        sentry_span = span_map.get(span_id, None)
        if not sentry_span:
            return

        setter.set(carrier, SENTRY_TRACE_HEADER_NAME, sentry_span.to_traceparent())

        baggage = sentry_span.get_baggage()
        if baggage:
            setter.set(carrier, BAGGAGE_HEADER_NAME, baggage)

    @property
    def fields(self) -> typing.Set[str]:
        return {
            self.TRACE_ID_KEY,
            self.SPAN_ID_KEY,
            self.SAMPLED_KEY,
        }
