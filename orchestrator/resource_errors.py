"""Content-free resource refusal type; safe for uncomposed native route imports."""


class ResourceError(ValueError):
    """Closed codes only. Unknown commit cannot be interpreted as absence."""

    def __init__(self, code: str = "uncertain") -> None:
        self.code = code
        super().__init__(code)
