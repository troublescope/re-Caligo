import logging
import os
from typing import TYPE_CHECKING, Any

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from .base import CaligoBase

if TYPE_CHECKING:
    from .bot import Caligo

IS_TERMUX = False
if os.getenv("TERMUX__UID"):
    IS_TERMUX = True


class DatabaseProvider(CaligoBase):
    db: AsyncDatabase
    log: logging.Logger

    def __init__(self: "Caligo", **kwargs: Any) -> None:
        if IS_TERMUX:
            self.log.info("Set DNS resolver for Termux enviroment")
            import dns.asyncresolver

            async_resolver = dns.asyncresolver.Resolver(configure=False)
            async_resolver.nameservers = ["8.8.8.8", "8.8.4.4"]
            dns.asyncresolver.default_resolver = async_resolver

        client = AsyncMongoClient(self.config["bot"]["db_uri"], connect=False)
        self.db = client.get_database("CALIGO")

        # Propagate initialization to other mixins
        super().__init__(**kwargs)
