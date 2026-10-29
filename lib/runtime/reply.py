"""A reply string that remembers whether its content may be logged."""


class Reply(str):
    """The reply text, plus ``sensitive``: True when it carries data that must stay out of
    logs (e.g. cluster state from ``cluster_agent`` or a ``/k`` command). Transports log a
    placeholder for it (:func:`loggable`)."""

    sensitive: bool = False

    def __new__(cls, text: str, sensitive: bool = False):
        obj = super().__new__(cls, text)
        obj.sensitive = sensitive
        return obj


def loggable(reply) -> str:
    """What a transport may write to its log for ``reply``."""
    if getattr(reply, "sensitive", False):
        return f"<treść poufna pominięta, {len(reply)} znaków>"
    return str(reply)
