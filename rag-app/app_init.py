import logging
import os

import toml


class MissingEnvironmentVariable(Exception):
    """Raised if a required environment variable is missing"""


DEFAULT_LOG_LEVEL = logging.INFO
LOGGER = logging.getLogger(__name__)
LOGGING_FORMAT = "%(asctime)s %(levelname)-5.5s " \
                 "[%(name)s]:[%(threadName)s] " \
                 "%(message)s"


if __name__ == "__main__":
    streamlit_secrets = {}

    # logging configuration
    log_level = DEFAULT_LOG_LEVEL
    if os.environ.get("VERBOSE", "").lower() == "true":
        log_level = logging.DEBUG
    logging.basicConfig(level=log_level, format=LOGGING_FORMAT)

    # Default to the user profile location for local dev; container can override via env var.
    default_secrets_path = os.path.join(os.path.expanduser("~"), ".streamlit", "secrets.toml")
    secrets_path = os.environ.get("STREAMLIT_SECRETS_PATH", default_secrets_path)
    os.makedirs(os.path.dirname(secrets_path), exist_ok=True)

    LOGGER.info("Writing streamlit secrets to %s", secrets_path)
    with open(secrets_path, "w") as file:
        toml.dump(streamlit_secrets, file)
