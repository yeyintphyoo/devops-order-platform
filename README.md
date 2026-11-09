# DevOps Order Platform

A **local-first DevOps portfolio project** demonstrating backend development, containerization, relational data, caching, asynchronous messaging, reliable event publishing, observability, alerting, and application security.

The platform is developed incrementally. Each lab milestone emphasizes understanding architecture, testing failure scenarios, preserving state, and documenting what has actually been verified.

> **Milestone:** Transactional outbox reliability, observability, repository hygiene, and GitHub synchronization verified through **Step 3S.23.17**. Commit: [`dfde702`](https://github.com/yeyintphyoo/devops-order-platform/commit/dfde702). This is a local lab, **not a production-readiness certification**.

## Project status

### Implemented and verified in the lab

- **API and persistence:** FastAPI, PostgreSQL, SQLAlchemy, health endpoints, product functionality, and Alembic migrations for orders, outbox events, and processed events.
- **Caching and messaging:** Redis integration; RabbitMQ integration and asynchronous event-processing components.
- **Transactional outbox:** Outbox records, publisher, event claiming/retry handling, consumer worker implementation, and processed-event tracking.
- **Containerization:** Docker Compose and backend container security controls.
- **Application observability:** Prometheus application metrics, Grafana application dashboard, and HTTP 5xx/backend-availability alert tests with SMTP firing and recovery notifications.
- **Outbox observability:** Dedicated Prometheus exporter, Grafana transactional outbox dashboard, five Prometheus reliability alerts, and five synthetic alert-rule tests.
- **Monitoring security:** Least-privilege PostgreSQL monitoring role for the outbox exporter.
- **Quality and Git:** 65 Python tests passed in the most recent recorded run; development dependency lockfile repaired; Git exclusions reviewed; 23-file feature commit pushed and synchronized with `origin/main`.

### Planned / not yet verified as complete

- Nginx reverse proxy.
- Jenkins CI/CD pipeline improvements and automated deployment.
- OpenTelemetry distributed tracing.
- Kubernetes deployment.
- AWS deployment.
- Further production hardening, isolated integration testing, and operational runbooks.

> **Reliability-lab state:** The RabbitMQ consumer is **intentionally stopped**. This is not evidence of a fully running end-to-end consumer workflow. Do not start the consumer or drain/purge queues merely to follow this README.

## Architecture

Conceptual local architecture (not a claim that every component runs continuously):

```text
                     Client / Developer
                            |
                            v
                       FastAPI API
                       /   |    \
                      v    v     v
                PostgreSQL Redis RabbitMQ
                  |                  ^
                  v                  |
           Orders + Outbox --> Outbox Publisher
                                      |
                              Event / Retry / DLQ
                                      |
                             Consumer Worker
                           (intentionally stopped)

    FastAPI /metrics ----+ 
                         |
    Outbox Exporter -----+----> Prometheus ----> Grafana
        (read-only DB)           |                 |
                                 v                 v
                           Outbox alerts     Dashboards / alerts
```

### Service responsibilities

| Component | Responsibility |
|---|---|
| FastAPI backend | HTTP API, health checks, business logic, application metrics |
| PostgreSQL | Persistent orders, transactional outbox, processed-event records |
| Redis | Cache integration |
| RabbitMQ | Event queues, retry and dead-letter handling |
| Outbox publisher | Claims/publishes eligible outbox events |
| Consumer worker | Event handling and processed-event tracking; intentionally stopped in the current reliability lab |
| Outbox exporter | Exposes read-only PostgreSQL outbox health metrics |
| Prometheus | Scrapes application/exporter metrics and evaluates outbox alert rules |
| Grafana | Application and transactional outbox dashboards; application alerting and SMTP notifications |

Services are orchestrated with Docker Compose. Database and monitoring state must be preserved when working with the existing environment.

## Technology stack

| Area | Technology |
|---|---|
| Backend | Python, FastAPI |
| Persistence | PostgreSQL, SQLAlchemy, Alembic |
| Caching | Redis |
| Messaging | RabbitMQ |
| Containers | Docker, Docker Compose |
| Monitoring | Prometheus, custom outbox exporter |
| Dashboards / application alerts | Grafana |
| Testing | pytest, Prometheus `promtool` |
| Code quality | Ruff; development dependency lockfile |
| Version control | Git, GitHub |

## Application endpoints

The previously documented application endpoints include:

| Endpoint | Purpose |
|---|---|
| `GET /health/live` | Application liveness |
| `GET /health/ready` | Application readiness |
| `GET /products` | List products |
| `GET /products/{product_id}` | Retrieve a product |
| `POST /products` | Create a product |
| `PATCH /products/{product_id}/stock` | Update stock |
| `GET /metrics` | Prometheus application metrics |

Additional order-related functionality is implemented in the codebase; consult the running FastAPI OpenAPI documentation for the exact current routes and request schemas.

### Temporary failure-testing endpoint

`GET /_test/500` deliberately returns HTTP 500 for local monitoring exercises. **Do not expose this endpoint in production.** It is intentionally retained for the current lab.

## Transactional outbox and reliability

The transactional outbox pattern stores business changes and outgoing event records in PostgreSQL, enabling a separate publisher to deliver events to RabbitMQ. The implementation includes claiming/retry logic and processed-event tracking. It is designed for reliability experiments, not a blanket guarantee of exactly-once delivery.

Three committed Alembic migrations cover:

1. Orders and outbox events.
2. Outbox event claiming and retry support.
3. Processed events.

### Preserved reliability-test state

The most recently verified lab snapshot was:

| Item | Recorded state |
|---|---:|
| `order.events.queue` ready messages | 4 |
| `order.events.retry.queue` ready messages | 0 |
| `order.events.dlq` ready messages | 1 |
| `order.events.step3s20.crash.queue` ready messages | 0 |
| Consumers / unacknowledged messages | 0 / 0 |
| Outbox records | 4 |
| Published outbox records | 4 |
| Pending / retry-pending / expired leases | 0 / 0 / 0 |

**These are historical test observations, not live guarantees.** Preserve the queue messages and outbox records for follow-up exercises. Do not consume, purge, delete, or reset them without a deliberate test plan.

## Monitoring and observability

### Prometheus

Prometheus configuration: `prometheus/prometheus.yml`. The original application setup used a 15-second scrape interval and included jobs for Prometheus itself and the FastAPI backend. The current lab also monitors the outbox exporter.

Useful example queries:

```promql
sum(rate(http_requests_total[5m]))
```

```promql
sum(rate(http_requests_total{status=~"5xx"}[5m]))
```

```promql
up{job="order-platform-backend"}
```

```promql
up{job="order-platform-outbox"}
```

```promql
order_platform_outbox_exporter_db_up
```

Metric labels and available series depend on instrumentation, scrape configuration, and traffic.

### Grafana dashboards

- **DevOps Order Platform - Application Monitoring:** request rate, HTTP 5xx rate, backend availability, and request rate by endpoint.
- **DevOps Order Platform — Transactional Outbox:** ten metrics/panels covering outbox reliability and exporter health; verified in the local Grafana lab. Dashboard provisioning/export into Git has **not** been verified as part of commit `dfde702`.

The earlier application alert exercises verified both firing and recovery SMTP notifications. The SMTP password is supplied through `GRAFANA_SMTP_PASSWORD`, not hardcoded in the Compose file.

### Application alerts (Grafana)

| Alert | Test performed | Verified outcome |
|---|---|---|
| `Alert_Backend_5xx` | Triggered temporary HTTP 500 responses, then stopped | Firing and recovery notifications received |
| `Alert_Backend_Down` | Stopped and restarted backend in an earlier controlled exercise | Target DOWN/UP transitions and notifications verified |

A Prometheus `up` value of `1` confirms successful scraping, **not** full application functionality.

### Transactional outbox alerts (Prometheus)

Alert definitions: `prometheus/alerts/outbox-alerts.yml`.

| Alert | Detects |
|---|---|
| `OutboxBacklogAging` | Aging unpublished outbox backlog |
| `OutboxExpiredLeases` | Expired event claims/leases |
| `OutboxExporterDatabaseDown` | Exporter cannot reach its database |
| `OutboxExporterDown` | Exporter scrape target unavailable |
| `OutboxMetricsStale` | Outbox metrics no longer fresh |

Synthetic tests: `prometheus/tests/outbox-alerts.test.yml`. **All five synthetic alert tests passed** using `promtool test rules`; `promtool check config` also passed. At the last verification, the exporter database-up metric and exporter scrape `up` were `1`, and all five outbox alerts were inactive. This validates rule behavior in synthetic tests, **not** live delivery of five alert notifications.

## Security

### Backend container controls

The earlier local Compose setup documented:

- Read-only container root filesystem.
- Dropped Linux capabilities.
- `no-new-privileges`.
- Health checks.
- Database credentials supplied through environment variables.

### Outbox exporter database permissions

The exporter uses a dedicated PostgreSQL role, `outbox_monitor`, with `CONNECT` on `orders_db`, `USAGE` on schema `public`, and `SELECT` on `public.outbox_events`. It has **no** `INSERT`, `UPDATE`, `DELETE`, or `TRUNCATE` privileges on that table.

The local `.env.outbox-exporter` is Git-ignored and was configured with restrictive permissions (`chmod 600`). Do not commit real credentials.

### Repository hygiene

The verified feature commit includes `.gitignore` exclusions for local exporter credentials and selected backup files. A staged-filename check found no `.env`, backup, private-key, or experimental Dockerfile paths among the 23 committed files. **This was not a comprehensive secret or Git-history scan.**

Three experimental files remain intentionally untracked:

```text
backend/Dockerfile.multistage-test
backend/Dockerfile.runtime-test
backend/Dockerfile.security-test
```

## Local development

### Prerequisites

- Git.
- Docker Engine and Docker Compose.
- A compatible Python version and virtual environment.
- Browser for Grafana and Prometheus.

### Clone

```bash
git clone https://github.com/yeyintphyoo/devops-order-platform.git
cd devops-order-platform
```

### Configure local secrets

Create the local environment files required by your Compose configuration. For example, the existing setup documented:

```dotenv
POSTGRES_DB=orders_db
POSTGRES_USER=orders_user
POSTGRES_PASSWORD=replace_with_a_local_password
GRAFANA_SMTP_PASSWORD=replace_with_your_smtp_app_password
```

RabbitMQ and exporter credentials must also match the current `compose.yaml` and local `env_file` configuration. **This snippet is illustrative, not a complete or validated environment template.** Never commit real `.env` files or passwords.

### Start a fresh environment

For a **fresh, disposable environment only**, after configuring required variables:

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

For the **existing reliability lab**, do **not** blindly run `docker compose up -d --build`: doing so could start the intentionally stopped consumer or alter the test state. Inspect the Compose services and current containers first, and start only explicitly approved services.

### Basic checks

When the API is running and its port is published locally:

```bash
curl -i http://127.0.0.1:8000/health/live
curl -i http://127.0.0.1:8000/health/ready
curl -s http://127.0.0.1:8000/metrics | head
```

Previously documented local monitoring addresses:

| Interface | URL |
|---|---|
| Backend API | `http://127.0.0.1:8000` |
| Prometheus | `http://127.0.0.1:9090` |
| Grafana | `http://127.0.0.1:3000` |

Actual port mappings depend on the current Compose configuration.

## Testing and validation

### Python tests

The latest recorded test run completed with **65 passed**. In an isolated development/test environment:

```bash
python -m pytest -q
```

Install dependencies from the project requirements and development lockfile as appropriate. **Before running tests against the existing lab, verify that fixtures and environment variables cannot modify its PostgreSQL data or RabbitMQ queues.**

### Prometheus rule validation

The following checks passed in the lab using the Prometheus `promtool` tooling:

```bash
promtool check config prometheus/prometheus.yml
promtool test rules prometheus/tests/outbox-alerts.test.yml
```

If running `promtool` in a container, adjust paths to the container's mounted configuration paths. These commands are documentation examples; ensure the tool is installed before running them locally.

### Git milestone

```text
Commit: dfde702
Branch: main
Remote: origin/main
Message: feat: implement reliable order processing with transactional outbox
Changes: 23 files, 2,753 insertions, 11 deletions
Push: successful; local main and origin/main synchronized at verification time
```

## Operational safety

- **Never** use `docker compose down -v` in the existing lab.
- Do not run `docker compose down --remove-orphans` or remove reliability-test containers as part of routine cleanup.
- Do not purge, consume, or delete RabbitMQ messages, including the dead-letter queue.
- Do not modify or delete existing PostgreSQL outbox records to make a test pass.
- Keep the consumer stopped until a dedicated test step explicitly calls for starting it.
- Preserve PostgreSQL, Prometheus, and Grafana persistent volumes.
- Keep credentials and local backup files out of Git.
- Keep `/_test/500` restricted to local testing.
- Verify both alert firing **and** recovery when performing live alert exercises.

## Development roadmap

| Phase / milestone | Goal | Verified status |
|---|---|---|
| 1 | FastAPI application and PostgreSQL integration | Implemented |
| 2 | Docker Compose and backend security | Implemented / lab-verified |
| 3 | Prometheus application metrics | Implemented |
| 4 | Grafana application dashboard and SMTP alerts | Firing and recovery tested |
| 5 | Redis caching | Implemented |
| 6 | RabbitMQ messaging and transactional outbox | Implemented; reliability scenarios tested; consumer intentionally stopped |
| 3S.23.12 | Grafana transactional outbox dashboard | Lab-verified |
| 3S.23.13 | Five Prometheus outbox alerts | Configuration validated |
| 3S.23.14 | Five synthetic alert-rule tests | Passed |
| 3S.23.15 | Least-privilege PostgreSQL exporter role | Verified |
| 3S.23.16 | Git hygiene and selective staging | Completed |
| 3S.23.17 | Commit and GitHub synchronization | Completed — `dfde702` |
| Next | Jenkins CI validation in isolated test environment | Planned |
| Future | Nginx, OpenTelemetry, Kubernetes, AWS | Planned |

## Next milestone: CI/CD

The proposed next phase is a Jenkins pipeline for source checkout, dependency installation, linting, unit tests, coverage, Docker image building, and security scanning. Start with **CI-only checks in an isolated environment**; defer deployment and any integration tests that could touch the existing outbox or RabbitMQ state.

---

**Scope note:** This README records milestone results observed during development. A passing test, successful push, or inactive alert does not independently establish production readiness or guarantee current runtime health.
