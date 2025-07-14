import os
from typing import TYPE_CHECKING, Any

from .base import CaligoBase
from .database import AsyncClient, AsyncDatabase

if TYPE_CHECKING:
    from .bot import Caligo

IS_TERMUX = False
if os.getenv("TERMUX__UID"):
    IS_TERMUX = True

class DatabaseProvider(CaligoBase):
    db: AsyncDatabase

    def __init__(self: "Caligo", **kwargs: Any) -> None:
        if IS_TERMUX:
            import dns.resolver
            dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
            dns.resolver.default_resolver.nameservers = ["8.8.4.4"]
            
        client = AsyncClient(self.config["bot"]["db_uri"], connect=False)
        self.db = client.get_database("CALIGO")

        # Propagate initialization to other mixins
        super().__init__(**kwargs)