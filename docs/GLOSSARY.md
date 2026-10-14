# Hermes Agent Observability

Terms for describing whether captured session events support trustworthy absence claims and cross-session comparison.

## Coverage

**Coverage state**:
A per-session, per-event-family/tool classification of whether telemetry was not captured, is partial, is observed with no known gaps, or cannot be assessed.

**Not captured**:
No telemetry was collected for the session or event family, such as when collection began after the session started or the event family is unsupported.

**Partial**:
Some telemetry is present, but a known gap or incomplete lifecycle makes absence claims unreliable.

**Observed—no known gaps**:
Capture was eligible and supported, with no detected gaps in the observed lifecycle. It is not proof that no event was silently lost.
_Avoid_: Complete, fully captured

**Unknown**:
Available metadata or schema information is insufficient to assign a reliable coverage state.

**Known zero**:
No matching event was observed for a session/event family whose state is Observed—no known gaps. It must not be inferred for Not captured, Partial, or Unknown coverage.
