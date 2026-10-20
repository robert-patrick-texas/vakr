"""Result types and terminal rendering for the step-by-step egress diagnosis.

``diagnose`` produces one ``Diagnosis`` per URL; ``vakr doctor --network``
turns its steps into check lines and ``self-update --check`` prints
``render`` output, so an operator sees the same steps in both (EGRESS-005).
The hint wording depends on whether an untrusted issuer or only the strict
X.509 profile broke the handshake (EGRESS-007).

Dependencies:
    dataclasses: Immutable result records.

Safety:
    Pure data and formatting; no I/O.

Example:
    >>> render(Diagnosis("https://h/", (Step("dns", True, "h -> 192.0.2.1"),)))
    ['egress https://h/: ok', '  [ok  ] dns: h -> 192.0.2.1']
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Step:
    """Result of one diagnosis step.

    Attributes:
        name: ``"dns"``, ``"tcp"``, ``"proxy"``, ``"tls"``, or ``"http"``.
        ok: Whether the step succeeded.
        detail: What was observed.
    """

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class Diagnosis:
    """All steps run for one URL, stopping at the first failure.

    Attributes:
        url: The URL diagnosed.
        steps: Steps in execution order.
        hint: Suggested remedy, when one is known.
        strict_profile: The failure comes only from the strict X.509 profile.
    """

    url: str
    steps: tuple[Step, ...]
    hint: str | None = None
    strict_profile: bool = False

    @property
    def ok(self) -> bool:
        """Whether every step succeeded.

        Args:
            None

        Returns:
            bool: ``True`` when no step failed.

        Raises:
            None
        """
        return all(step.ok for step in self.steps)


def render(diagnosis: Diagnosis) -> list[str]:
    """Format a diagnosis as lines for a terminal or ticket.

    Args:
        diagnosis: Result of ``diagnose``.

    Returns:
        list[str]: Header, one line per step, and the hint when present.

    Raises:
        None
    """
    verdict = "ok" if diagnosis.ok else "FAILED"
    lines = [f"egress {diagnosis.url}: {verdict}"]
    lines += [f"  [{'ok' if s.ok else 'FAIL':<4}] {s.name}: {s.detail}" for s in diagnosis.steps]
    if diagnosis.hint and diagnosis.strict_profile:
        lines.append("  hint: the chain is trusted but breaks the strict X.509 profile;")
        lines.append(f"        {diagnosis.hint}")
    elif diagnosis.hint:
        lines.append("  hint: if that issuer is your organization's TLS-inspection CA,")
        lines.append(f"        {diagnosis.hint}")
    return lines
