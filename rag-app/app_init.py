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

    # Keep an empty secrets file so Streamlit startup remains unchanged.
    LOGGER.info("Writing streamlit secrets")
    with open("/root/.streamlit/secrets.toml", "w") as file:
        toml.dump(streamlit_secrets, file)
