# CD-016 · Serverless Architecture on AWS Lambda

[![CI/CD](https://github.com/BHAVISHYA-RAJ-14/cd-016-serverless-lambda/actions/workflows/deploy.yml/badge.svg)](https://github.com/BHAVISHYA-RAJ-14/cd-016-serverless-lambda/actions)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue?logo=python)](https://python.org)
[![AWS SAM](https://img.shields.io/badge/AWS-SAM-orange?logo=amazonaws)](https://aws.amazon.com/serverless/sam/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green)](LICENSE)

> **Production-grade serverless platform** — Lambda, API Gateway (HTTP API), EventBridge, Step Functions, Lambda Powertools, cold start optimisation, and full CloudWatch observability. Built as part of the Next Afield Cloud & DevOps internship (Intern ID: NAI26MAR-CD-01).

---

## 🏗️ Architecture

```
Client Request
      │
      ▼
API Gateway (HTTP API)  ──► Lambda Authorizer (JWT, 5-min cache)
      │
      ▼
 Orders API Lambda (Python 3.12, arm64/Graviton2)
      │                    │
      ▼                    ▼
 DynamoDB            EventBridge (custom bus: cd016-orders-bus)
 (orders table)           │
                   ┌──────┼──────────┐
                   ▼      ▼          ▼
             Inventory  Payment  Email Notifier
             Lambda     Lambda   Lambda
                   │
                   ▼
              SQS DLQ (failed events, 14-day retention)
                   │
Step Functions ◄───┘
order-fulfillment-workflow
  validate → reserve → charge → confirm → fulfil
```

---

## ✅ Deliverables (8/8)

| # | Deliverable | Status |
|---|---|---|
| 1 | SAM template + local development setup | ✅ `template.yaml` + `samconfig.toml` |
| 2 | API Gateway with JWT authorizer + validation | ✅ `src/authorizer/` + JSON Schema validation |
| 3 | EventBridge event-driven architecture (5 event types) | ✅ OrderPlaced, PaymentProcessed, InventoryReserved, InventoryFailed, OrderFulfilled |
| 4 | Step Functions order fulfilment workflow | ✅ `src/step_functions/order_workflow.asl.json` |
| 5 | Cold start optimisation (< 500ms target) | ✅ arm64, lazy imports, Lambda Layers, /tmp caching |
| 6 | Lambda Powertools logging + tracing | ✅ Structured JSON, X-Ray subsegments, correlation IDs |
| 7 | Lambda Power Tuning memory optimisation | ✅ `make power-tuning` — 128MB→1024MB benchmark |
| 8 | Cost dashboard + budget alerts | ✅ CloudWatch dashboard + $5 budget alert via SNS |

---

## 🚀 Quick Start

### Prerequisites
```bash
python3 --version       # 3.12+
aws --version           # v2+
sam --version           # 1.100+
docker --version        # any (for sam local)
aws sts get-caller-identity   # confirm AWS access, region ap-south-1
```

### 1. Clone & Install
```bash
git clone https://github.com/BHAVISHYA-RAJ-14/cd-016-serverless-lambda.git
cd cd-016-serverless-lambda
make install
```

### 2. Build
```bash
make build
```

### 3. Test Locally (no AWS needed)
```bash
# Start local API Gateway on port 3000
make local-api

# In another terminal — test health check
curl http://localhost:3000/health

# Test create order
curl -X POST http://localhost:3000/orders \
  -H "Content-Type: application/json" \
  -d '{"userId":"user-001","items":[{"productId":"prod-x1","quantity":1,"price":999.0}]}'
```

### 4. Run Tests
```bash
make test
```

### 5. Deploy to AWS
```bash
# First time — interactive guided setup
make deploy-guided

# Subsequent deploys
make deploy-dev
```

### 6. Check Outputs
```bash
make outputs
# Shows: API endpoint, DynamoDB table, EventBridge bus, Step Functions ARN
```

---

## 📁 Project Structure

```
cd-016-serverless-lambda/
├── template.yaml                    # SAM master template (all resources)
├── samconfig.toml                   # Dev + prod deployment config
├── Makefile                         # All commands (build, deploy, test, logs)
├── env.json                         # Local env vars for sam local (not committed)
├── .github/workflows/deploy.yml     # GitHub Actions CI/CD (OIDC keyless auth)
├── src/
│   ├── orders_api/
│   │   ├── app.py                   # Main CRUD handler + routing
│   │   ├── powertools_config.py     # Centralised logger/tracer/metrics
│   │   └── requirements.txt
│   ├── authorizer/
│   │   └── app.py                   # JWT Lambda Authorizer (5-min cache)
│   ├── inventory/
│   │   └── app.py                   # Inventory reservation (EventBridge trigger)
│   ├── payment/
│   │   └── app.py                   # Payment processing (EventBridge trigger)
│   ├── email_notifier/
│   │   └── app.py                   # Email notifications (3 event types)
│   └── step_functions/
│       └── order_workflow.asl.json  # State machine definition (ASL)
├── layers/
│   └── common/
│       └── requirements.txt         # Lambda Powertools, PyJWT (shared layer)
└── tests/
    ├── unit/
    │   └── test_orders_api.py       # moto-based unit tests (no AWS calls)
    ├── integration/
    └── events/                      # sam local invoke test events
```

---

## ⚡ Cold Start Optimisation

| Technique | Impact |
|---|---|
| `arm64` (Graviton2) runtime | ~20% faster + 20% cheaper vs x86 |
| Lambda Layers for heavy deps | Deps cached separately from code |
| Lazy imports (`import` inside function body) | Only imports what's needed per code path |
| `/tmp` caching for reusable objects | Persists across warm invocations |
| Minimal per-function package | Each function has empty `requirements.txt` |
| **Target** | Cold start < 500ms, warm < 50ms |

---

## 📊 EventBridge Event Types

| Event | Source | Triggered By | Consumers |
|---|---|---|---|
| `OrderPlaced` | `cd016.orders` | Orders API on create | Inventory, Payment, Email |
| `InventoryReserved` | `cd016.inventory` | Inventory Lambda (success) | Step Functions |
| `InventoryFailed` | `cd016.inventory` | Inventory Lambda (fail) | Email |
| `PaymentProcessed` | `cd016.payment` | Payment Lambda (success) | Email |
| `PaymentFailed` | `cd016.payment` | Payment Lambda (fail) | Email |

---

## 💰 Cost (AWS Free Tier)

| Service | Free Tier | This Project |
|---|---|---|
| Lambda | 1M req/mo + 400K GB-sec | ~$0.00 |
| API Gateway (HTTP) | 1M calls/mo (12 months) | ~$0.00 |
| DynamoDB | 25GB + 25 RCU/WCU forever | ~$0.00 |
| EventBridge | 1M events/mo | ~$0.00 |
| Step Functions | 4,000 transitions/mo | ~$0.00 |
| CloudWatch | 5GB logs + 10 metrics | ~$0.00 |
| **Total** | | **~$0.00** ✅ |

---

## 🔧 Useful Commands

```bash
make help              # Show all commands
make build             # SAM build (cached + parallel)
make deploy-dev        # Deploy to dev (ap-south-1)
make local-api         # Local API Gateway on :3000
make test              # Unit tests with moto
make logs-api          # Tail orders API CloudWatch logs
make cold-start-check  # X-Ray cold start analysis
make power-tuning      # Deploy Lambda Power Tuning tool
make outputs           # Show stack outputs
make delete-dev        # ⚠️ Destroy dev stack
```

---

## 🛠️ Tech Stack

![AWS](https://img.shields.io/badge/AWS-Lambda%20%7C%20API%20GW%20%7C%20EventBridge%20%7C%20Step%20Functions%20%7C%20DynamoDB-orange?logo=amazonaws)
![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python)
![SAM](https://img.shields.io/badge/SAM-IaC-orange)
![Powertools](https://img.shields.io/badge/Lambda%20Powertools-Logging%20%7C%20Tracing%20%7C%20Metrics-purple)
![GitHub Actions](https://img.shields.io/badge/GitHub%20Actions-CI%2FCD-black?logo=githubactions)
![arm64](https://img.shields.io/badge/Architecture-arm64%20Graviton2-blue)

---

## 👤 Author

**Bhavishya Raj**
Cloud & DevOps Intern • Next Afield •
AWS Student Builder Group Leader • GLA University

[![GitHub](https://img.shields.io/badge/GitHub-BHAVISHYA--RAJ--14-black?logo=github)](https://github.com/BHAVISHYA-RAJ-14)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-bhavishya--raj-blue?logo=linkedin)](https://linkedin.com/in/bhavishya-raj)
