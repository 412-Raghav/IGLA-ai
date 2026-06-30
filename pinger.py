import logging
import os
import sys

import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("igla-pinger")

REFRESH_URL = os.getenv("REFRESH_URL")
REFRESH_TOKEN = os.getenv("REFRESH_TOKEN")


def main() -> None:
    if not REFRESH_URL or not REFRESH_TOKEN:
        logger.error("REFRESH_URL or REFRESH_TOKEN not set; aborting.")
        sys.exit(1)

    headers = {"X-Refresh-Token": REFRESH_TOKEN}

    try:
        response = httpx.post(REFRESH_URL, headers=headers, timeout=30.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Refresh ping failed: %s", exc)
        sys.exit(1)

    logger.info("Refresh ping succeeded: %s %s", response.status_code, response.text)


if __name__ == "__main__":
    main()