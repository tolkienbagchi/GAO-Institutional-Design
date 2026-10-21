"""Timeout policies a layer declares for decisions that are not answered in time."""

from typing import Literal

from gao_idl.core.schemas import FrozenModel


class Fail(FrozenModel):
    """Fail the whole process."""

    kind: Literal["fail"] = "fail"


class DefaultAction(FrozenModel):
    """Substitute the layer's ``default_action``."""

    kind: Literal["default"] = "default"


class DropRole(FrozenModel):
    """Remove the role from the layer; it contributes no action."""

    kind: Literal["drop"] = "drop"


TimeoutPolicy = Fail | DefaultAction | DropRole
