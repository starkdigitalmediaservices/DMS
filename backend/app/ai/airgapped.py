from app.config import settings
from app.ai.egress_guard import install_egress_guard


class AirGappedViolation(RuntimeError):
    """Raised when AIR_GAPPED=true and a call site would otherwise reach an external API."""


# T92 — install the network-level guard as a side effect of importing this
# module. Both the API process (main.py) and the Celery worker
# (tasks/worker.py) import through app/ai/factory.py and app/ocr/factory.py,
# which both import this module — so this runs in both processes without
# needing separate startup wiring in each. No-op when air_gapped is False.
if settings.air_gapped:
    install_egress_guard()


def enforce_local(surface: str, provider_name: str) -> None:
    """T91 (partial) — call before resolving any provider that reaches an
    external API. Refuses to start rather than silently calling out, per
    docs/planning/build_design.txt section 11: 'a missing local model raises an error,
    never silently calls an API.'
    """
    if settings.air_gapped:
        raise AirGappedViolation(
            f"AIR_GAPPED=true but '{surface}' is configured to use '{provider_name}', "
            f"which calls an external API. No local provider is wired up for this "
            f"surface yet (see backlog T90). Refusing to start rather than silently "
            f"sending data outside the network."
        )
