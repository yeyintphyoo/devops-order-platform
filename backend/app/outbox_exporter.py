import logging
import os
import time

from prometheus_client import Gauge, start_http_server
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger("outbox-exporter")

EXPORTER_PORT = int(os.getenv("OUTBOX_EXPORTER_PORT", "9108"))
POLL_INTERVAL = int(os.getenv("OUTBOX_EXPORTER_POLL_INTERVAL", "15"))

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL must be configured")

if make_url(DATABASE_URL).get_backend_name() != "postgresql":
    raise RuntimeError("Outbox exporter requires PostgreSQL")


engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=1,
    max_overflow=0,
    connect_args={
        "options": "-c default_transaction_read_only=on"
    },
)


METRIC_NAMES = {
    "total_events": "order_platform_outbox_total_events",
    "published_events": "order_platform_outbox_published_events",
    "pending_events": "order_platform_outbox_pending_events",
    "retry_pending_events": "order_platform_outbox_retry_pending_events",
    "active_leases": "order_platform_outbox_active_leases",
    "expired_leases": "order_platform_outbox_expired_leases",
    "retry_due_events": "order_platform_outbox_retry_due_events",
    "max_retry_count": "order_platform_outbox_max_retry_count",
    "oldest_pending_age_seconds":
        "order_platform_outbox_oldest_pending_age_seconds",
}


METRICS = {
    column: Gauge(metric_name, f"Outbox metric: {column}")
    for column, metric_name in METRIC_NAMES.items()
}


DB_UP = Gauge(
    "order_platform_outbox_exporter_db_up",
    "Whether the latest PostgreSQL outbox query succeeded",
)

LAST_SUCCESS = Gauge(
    "order_platform_outbox_exporter_last_success_timestamp_seconds",
    "Unix timestamp of the last successful outbox query",
)


QUERY = text("""
SELECT
    COUNT(*) AS total_events,

    COUNT(*) FILTER (
        WHERE published_at IS NOT NULL
    ) AS published_events,

    COUNT(*) FILTER (
        WHERE published_at IS NULL
    ) AS pending_events,

    COUNT(*) FILTER (
        WHERE published_at IS NULL
          AND retry_count > 0
    ) AS retry_pending_events,

    COUNT(*) FILTER (
        WHERE published_at IS NULL
          AND locked_until > NOW()
    ) AS active_leases,

    COUNT(*) FILTER (
        WHERE published_at IS NULL
          AND locked_until <= NOW()
    ) AS expired_leases,

    COUNT(*) FILTER (
        WHERE published_at IS NULL
          AND retry_count > 0
          AND (
              next_attempt_at IS NULL
              OR next_attempt_at <= NOW()
          )
    ) AS retry_due_events,

    COALESCE(MAX(retry_count), 0) AS max_retry_count,

    COALESCE(
        EXTRACT(
            EPOCH FROM (
                NOW() - MIN(created_at) FILTER (
                    WHERE published_at IS NULL
                )
            )
        ),
        0
    ) AS oldest_pending_age_seconds

FROM outbox_events
""")


def collect_outbox_metrics():
    try:
        with engine.connect() as connection:
            result = connection.execute(QUERY).mappings().one()

        # Update all metric values only after the query succeeds.
        for column, gauge in METRICS.items():
            gauge.set(float(result[column]))

        LAST_SUCCESS.set(time.time())
        DB_UP.set(1)

        logger.info(
            "Outbox metrics collected: pending=%s published=%s",
            result["pending_events"],
            result["published_events"],
        )

    except Exception:
        DB_UP.set(0)
        logger.exception("Failed to collect outbox metrics")


def main():
    if POLL_INTERVAL <= 0:
        raise ValueError("OUTBOX_EXPORTER_POLL_INTERVAL must be positive")

    logger.info(
        "Starting outbox exporter on port %s",
        EXPORTER_PORT,
    )

    # Collect immediately so the first scrape has meaningful values.
    collect_outbox_metrics()

    start_http_server(
        EXPORTER_PORT,
        addr="0.0.0.0",
    )

    while True:
        time.sleep(POLL_INTERVAL)
        collect_outbox_metrics()


if __name__ == "__main__":
    main()
