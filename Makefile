# ─────────────────────────────────────────────────────────────────────────────
# CD-016 Serverless Architecture — Makefile
# Usage: make <target>
# ─────────────────────────────────────────────────────────────────────────────

.PHONY: help install build deploy-dev deploy-prod local-api local-invoke \
        test lint clean delete-dev delete-prod logs cold-start-check

# ── Config ────────────────────────────────────────────────────────────────────
REGION      := ap-south-1
STACK_DEV   := cd016-serverless-dev
STACK_PROD  := cd016-serverless-prod
FUNCTION    := cd016-orders-api-dev

# ─────────────────────────────────────────────────────────────────────────────
help: ## Show all available commands
	 @echo ""
	 @echo "  CD-016 Serverless Architecture — Commands"
	 @echo "  ─────────────────────────────────────────"
	 @grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	 @echo ""

# ─────────────────────────────────────────────────────────────────────────────
install: ## Install local dev dependencies (for IDE + testing)
	pip install aws-lambda-powertools[all] PyJWT cryptography pytest pytest-mock boto3 moto

# ─────────────────────────────────────────────────────────────────────────────
build: ## SAM build (cached + parallel for speed)
	sam build --cached --parallel

build-no-cache: ## SAM build without cache (full rebuild)
	sam build --no-cached --parallel

# ─────────────────────────────────────────────────────────────────────────────
deploy-guided: ## First time deploy — runs sam deploy --guided (interactive)
	sam build --cached --parallel
	sam deploy --guided --region $(REGION)

deploy-dev: build ## Deploy to dev environment
	sam deploy \
		--config-env default \
		--region $(REGION) \
		--no-fail-on-empty-changeset

deploy-prod: build ## Deploy to prod environment (requires confirmation)
	sam deploy \
		--config-env prod \
		--region $(REGION) \
		--confirm-changeset

# ─────────────────────────────────────────────────────────────────────────────
local-api: build ## Start local API Gateway on port 3000
	sam local start-api \
		--port 3000 \
		--env-vars env.json \
		--region $(REGION) \
		--debug

local-invoke-health: build ## Test: invoke health check locally
	sam local invoke OrdersApiFunction \
		--event tests/events/health_check.json \
		--env-vars env.json \
		--region $(REGION)

local-invoke-create: build ## Test: invoke create order locally
	sam local invoke OrdersApiFunction \
		--event tests/events/create_order.json \
		--env-vars env.json \
		--region $(REGION)

local-invoke-authorizer: build ## Test: invoke JWT authorizer locally
	sam local invoke AuthorizerFunction \
		--event tests/events/authorizer_valid.json \
		--env-vars env.json \
		--region $(REGION)

# ─────────────────────────────────────────────────────────────────────────────
test: ## Run all unit tests
	python -m pytest tests/unit/ -v --tb=short

test-integration: ## Run integration tests (requires deployed stack)
	python -m pytest tests/integration/ -v --tb=short

test-coverage: ## Run tests with coverage report
	python -m pytest tests/unit/ --cov=src --cov-report=html --cov-report=term

lint: ## Run linting checks
	python -m flake8 src/ --max-line-length=120 --ignore=E501,W503
	python -m mypy src/ --ignore-missing-imports

# ─────────────────────────────────────────────────────────────────────────────
logs-api: ## Tail logs for orders API function
	sam logs -n OrdersApiFunction --stack-name $(STACK_DEV) --tail --region $(REGION)

logs-inventory: ## Tail logs for inventory function
	sam logs -n InventoryFunction --stack-name $(STACK_DEV) --tail --region $(REGION)

logs-payment: ## Tail logs for payment function
	sam logs -n PaymentFunction --stack-name $(STACK_DEV) --tail --region $(REGION)

logs-stepfunctions: ## View Step Functions execution logs
	aws logs tail /aws/states/cd016-order-fulfillment-dev \
		--follow --region $(REGION)

# ─────────────────────────────────────────────────────────────────────────────
cold-start-check: ## Check cold start times via X-Ray (run after deploy + invoke)
	 @echo "Fetching X-Ray traces for cold starts..."
	aws xray get-service-graph \
		--start-time $$(date -d '1 hour ago' +%s) \
		--end-time $$(date +%s) \
		--region $(REGION)

power-tuning: ## Deploy Lambda Power Tuning tool (one-time setup)
	 @echo "Deploying Lambda Power Tuning Tool from SAR..."
	aws serverlessrepo create-cloud-formation-change-set \
		--application-id arn:aws:serverlessrepo:us-east-1:451282441545:applications/aws-lambda-power-tuning \
		--semantic-version 4.3.3 \
		--stack-name lambda-power-tuning \
		--capabilities CAPABILITY_IAM \
		--region $(REGION)
	 @echo "Then: open Step Functions console → run aws-lambda-power-tuning"

# ─────────────────────────────────────────────────────────────────────────────
outputs: ## Show stack outputs (API endpoint, table name, etc.)
	aws cloudformation describe-stacks \
		--stack-name $(STACK_DEV) \
		--region $(REGION) \
		--query 'Stacks[0].Outputs' \
		--output table

describe: ## Describe the dev stack
	aws cloudformation describe-stacks \
		--stack-name $(STACK_DEV) \
		--region $(REGION)

# ─────────────────────────────────────────────────────────────────────────────
delete-dev: ## ⚠️  DELETE dev stack (irreversible — destroys all resources)
	@read -p "Are you sure you want to delete dev stack? [y/N] " confirm; \
		if [ "$$confirm" = "y" ]; then \
			aws cloudformation delete-stack --stack-name $(STACK_DEV) --region $(REGION); \
			echo "Deletion started for $(STACK_DEV)"; \
		else \
			echo "Deletion cancelled"; \
		fi

clean: ## Remove SAM build artifacts
	rm -rf .aws-sam/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
