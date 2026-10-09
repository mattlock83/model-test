class Inconclusive(RuntimeError):
    """The agent, evidence or infrastructure cannot establish the test outcome."""

    def __init__(self, message, *, result=None):
        super().__init__(message)
        self.result = result


class Defect(AssertionError):
    """Observed behavior violates a modeled requirement."""

    def __init__(self, message, *, result=None, case=None):
        super().__init__(message)
        self.result = result
        self.case = case
