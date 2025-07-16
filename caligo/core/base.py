from typing import TYPE_CHECKING, Any

__all__ = ["CaligoBase"]

if TYPE_CHECKING:
    from .bot import Caligo

    CaligoBase = Caligo
else:
    import abc

    CaligoBase: Any = abc.ABC
