# DevOps Order Platform

A local-first DevOps portfolio project demonstrating backend development, containerization, database integration, monitoring, alerting, and application security.

The project is being developed incrementally, with an emphasis on understanding system architecture, testing failure scenarios, and documenting operational behavior.

## Project Status

### Implemented

- FastAPI backend
- PostgreSQL database
- SQLAlchemy database integration
- Docker Compose orchestration
- Backend health-check endpoints
- Prometheus application metrics
- Prometheus monitoring and scraping
- Grafana application monitoring dashboard
- Grafana alert rules for HTTP 5xx errors and backend availability
- SMTP email notifications for alert firing and recovery
- Backend container security controls
- Automated backend tests

### Planned

- Redis caching
- RabbitMQ asynchronous order processing
- Nginx reverse proxy
- Jenkins CI/CD improvements
- OpenTelemetry distributed tracing
- Kubernetes deployment
- AWS deployment

## Architecture

The current local environment contains four main services:

                 DevOps Order Platform
                          |
                    User / Client
                          |
                          v
                   FastAPI Backend
                     Port 8000
                    /         \
                   v           v
              PostgreSQL    /metrics
                              |
                              v
                          Prometheus
                          Port 9090
                              ^
                              |
                           Grafana
                          Port 3000

### Services

| Service | Container | Purpose |
|---|---|---|
| FastAPI | `devops-order-api` | Application API, health checks, and metrics |
| PostgreSQL | `devops-order-postgres` | Persistent relational database |
| Prometheus | `devops-order-prometheus` | Metrics collection and time-series storage |
| Grafana | `devops-order-grafana` | Dashboards, alert evaluation, and email notifications |

The services communicate over the Docker Compose network named `order-platform`.

PostgreSQL data, Prometheus data, and Grafana data are stored in named Docker volumes.

## Technology Stack

- **Application:** Python and FastAPI
- **Database:** PostgreSQL
- **ORM:** SQLAlchemy
- **Containerization:** Docker
- **Orchestration:** Docker Compose
- **Monitoring:** Prometheus
- **Visualization and alerting:** Grafana
- **Testing:** pytest
- **Metrics instrumentation:** Prometheus FastAPI Instrumentator

## Application Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health/live` | Checks whether the application is alive |
| `GET /health/ready` | Checks application readiness |
| `GET /products` | Retrieves products |
| `GET /products/{product_id}` | Retrieves a product by ID |
| `POST /products` | Creates a product |
| `PATCH /products/{product_id}/stock` | Updates product stock |
| `GET /metrics` | Exposes Prometheus metrics |

### Temporary Testing Endpoint

The project currently includes:

`GET /_test/500`

This endpoint deliberately raises an exception to generate an HTTP 500 response for alert testing.

**This endpoint is temporary and must not be exposed in a production deployment.** It is retained in the current local environment for continued monitoring exercises.

## Prometheus Monitoring

Prometheus scrapes metrics at 15-second intervals.

Configuration file:

`prometheus/prometheus.yml`

Configured scrape jobs:

| Job | Target | Purpose |
|---|---|---|
| `prometheus` | `localhost:9090` | Monitors Prometheus itself |
| `order-platform-backend` | `backend:8000` | Collects FastAPI application metrics |

The backend exposes its metrics at `/metrics`.

### Example PromQL Queries

**HTTP request rate**

```promql
sum(rate(http_requests_total[5m]))
```

**HTTP 5xx error rate**

```promql
sum(rate(http_requests_total{status=~"5xx"}[5m]))
```

**Backend availability**

```promql
up{job="order-platform-backend"}
```

**Request rate by endpoint**

```promql
sum by (handler) (rate(http_requests_total[5m]))
```

The exact metric labels and available series depend on the instrumentation and collected traffic.

## Grafana Dashboard

Dashboard:

`DevOps Order Platform - Application Monitoring`

The dashboard includes panels for:

1. HTTP request rate
2. HTTP 5xx error rate
3. Backend availability
4. HTTP request rate by endpoint

Grafana is configured to send email notifications using SMTP.

The SMTP password is supplied through the `GRAFANA_SMTP_PASSWORD` environment variable rather than being hardcoded in the Compose file.

## Alert Rules

### 1. Alert_Backend_5xx

**Purpose:** Detect application-level HTTP 5xx errors.

Expression:

```promql
sum(rate(http_requests_total{status=~"5xx"}[5m]))
```

Condition: The expression is greater than zero.

Pending period: One minute.

Test performed:

1. Called the temporary HTTP 500 endpoint.
2. Verified that the backend returned HTTP 500 responses.
3. Confirmed that Prometheus recorded 5xx metrics.
4. Observed the Grafana alert enter Firing.
5. Received the firing email.
6. Stopped generating test errors.
7. Verified that the error rate returned to zero.
8. Received the recovery email.

**Result: Firing and recovery notifications verified.**

### 2. Alert_Backend_Down

**Purpose:** Detect when the backend target becomes unavailable to Prometheus.

Expression:

```promql
up{job="order-platform-backend"}
```

Condition: The expression is less than one.

Pending period: One minute.

Test performed:

1. Stopped the backend container.
2. Verified that the Prometheus backend target became DOWN.
3. Observed the Grafana alert enter Firing.
4. Received the firing email.
5. Restarted the backend container.
6. Verified that the container became healthy.
7. Confirmed that the Prometheus target returned to UP.
8. Received the recovery email.

**Result: Firing and recovery notifications verified.**

The Prometheus `up` metric describes scrape availability. A value of one means the target was scraped successfully; it does not guarantee that every application function is working correctly.

## Backend Container Security

The current backend service includes the following Docker Compose security settings:

- Read-only container root filesystem
- All Linux capabilities dropped
- `no-new-privileges` enabled
- Container health checks
- Database credentials supplied through environment variables

The current setup is a local development environment and is not, by itself, a complete production deployment.

## Prerequisites

Install the following tools:

- Git
- Docker Engine
- Docker Compose
- Python 3.10 or a compatible project-supported Python version
- A web browser

## Local Development

### 1. Clone the repository

```bash
git clone https://github.com/yeyintphyoo/devops-order-platform.git
cd devops-order-platform
```

### 2. Configure environment variables

Create a local `.env` file containing the required database and SMTP configuration.

Example structure:

```dotenv
POSTGRES_DB=orders_db
POSTGRES_USER=orders_user
POSTGRES_PASSWORD=replace_with_a_local_password
GRAFANA_SMTP_PASSWORD=replace_with_your_smtp_app_password
```

Use your own local credentials. Never commit `.env` or real passwords to Git.

### 3. Start the services

```bash
docker compose up -d --build
```

### 4. Check service status

```bash
docker compose ps
```

### 5. Check backend health

```bash
curl -i http://127.0.0.1:8000/health/live
```

```bash
curl -i http://127.0.0.1:8000/health/ready
```

### 6. Check Prometheus metrics

```bash
curl -s http://127.0.0.1:8000/metrics | head
```

### 7. Open the monitoring interfaces

| Interface | Local address |
|---|---|
| Backend API | `http://127.0.0.1:8000` |
| Prometheus | `http://127.0.0.1:9090` |
| Grafana | `http://127.0.0.1:3000` |

Grafana's initial login credentials depend on the configured Grafana account. Use your existing credentials.

## Testing

Run the backend test suite using the project's virtual environment:

```bash
python -m pytest -v
```

If running from a fresh environment, install the project's dependencies first according to its requirements and development dependency files.

## Operational Safety

- Preserve the PostgreSQL named volume when rebuilding or updating services.
- Avoid destructive volume-removal commands during routine development.
- Keep `.env` and real credentials out of Git.
- Keep the temporary HTTP 500 endpoint restricted to local testing.
- Verify service health and Prometheus targets after making infrastructure changes.
- Test both alert firing and recovery behavior, not just the initial notification.

## Development Roadmap

| Phase | Goal | Status |
|---|---|---|
| 1 | FastAPI application and PostgreSQL integration | Implemented |
| 2 | Docker Compose and backend security | Implemented |
| 3 | Prometheus metrics collection | Implemented |
| 4 | Grafana dashboards and email alerting | Tested |
| 5 | Redis caching | Planned |
| 6 | RabbitMQ asynchronous processing | Planned |
| 7 | Nginx reverse proxy | Planned |
| 8 | CI/CD improvements | In progress |
| 9 | OpenTelemetry tracing | Planned |
| 10 | Kubernetes deployment | Planned |
| 11 | AWS deployment | Planned |

The project will continue to evolve from a local development environment toward a more complete, observable, and automated application platform.
