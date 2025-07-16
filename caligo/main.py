import logging
import os
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib

from . import launch, log

config_path = Path("config.toml")
if not config_path.exists():
    config = None
else:
    with config_path.open(mode="rb") as f:
        config = tomllib.load(f)

# Inject db_uri from environment if available
if config:
    db_uri = os.getenv("DB_URI")
    if db_uri:
        config["bot"]["db_uri"] = db_uri

log.setup_log(config["bot"]["colorlog"] if config else False)

logs = logging.getLogger("Launch")
logs.info("Loading code")


def main():
    """Main entry point for the default bot command."""

    if not config:
        logs.error(
            "'config.toml' is missing, Configuration must be done before running the bot."
        )
        return

    launch.main(config)


if __name__ == "__main__":
    main()
