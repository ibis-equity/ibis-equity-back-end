# Module Guide: RAG with Amazon Bedrock and OpenSearch

This guide explains each module in the repository: what it does, how it connects to other modules, and what configuration it depends on.

## 1) Project entry and infrastructure orchestration

### `bin/rag-with-amazon-bedrock-and-opensearch.ts`
**Purpose**
- CDK app entrypoint that wires all stacks together.

**What it creates/wires**
- `BaseInfraStack`: foundational networking, storage, auth, and eventing.
- `TestComputeStack`: optional EC2 jump host.
- `OpenSearchStack`: OpenSearch Serverless collection + security/access policies.
- `OpenSearchUpdateStack`: Lambda for indexing processed documents.
- `RagAppStack`: ECS Fargate app service.

**Key integration flow**
- Passes shared resources (VPC, IAM roles, queue, secrets, target group, collection ID) from foundational stacks to dependent stacks.

---

## 2) CDK stack modules (`lib/`)

### `lib/base-infra-stack.ts`
**Purpose**
- Provisions all base AWS infrastructure required by the solution.

**Main resources**
- VPC with public/private subnets.
- S3 buckets:
  - knowledge base bucket (PDFs)
  - processed text bucket (TXT extracted from PDFs)
- PDF processor Lambda (`lambda/pdf-processor`) triggered by S3 object creation.
- SQS queue (`AOSS_Update_Queue`) and trigger Lambda (`lambda/aoss-trigger`) for decoupled indexing.
- Secrets Manager secret for OpenAI API key (`openAiApiKey`).
- IAM roles:
  - Lambda role for OpenSearch update API access.
  - ECS task role for `aoss:APIAccessAll` and `bedrock:InvokeModel`.
- ALB, target group, and Cognito authentication integration.
- EventBridge rules and helper Lambdas to normalize Cognito callback URLs.
- CloudTrail trail used for event-driven Cognito app client handling.

**Important environment/config dependencies**
- `CDK_DEFAULT_REGION` restricted to `us-east-1`, `us-west-2`, `eu-west-2`.
- `IAM_SELF_SIGNED_SERVER_CERT_NAME` required.
- Optional: `AOSS_INDEX_NAME`, `COGNITO_DOMAIN_NAME`, `DOCKER_CONTAINER_PLATFORM_ARCH`.

**Why it exists**
- Centralizes all shared platform pieces used by indexing and app-serving stacks.

### `lib/opensearch-stack.ts`
**Purpose**
- Creates and configures OpenSearch Serverless for vector search.

**Main resources**
- OpenSearch Serverless `VECTORSEARCH` collection.
- Network security policy (public access enabled for dashboard/collection).
- Encryption policy (AWS-owned key).
- Data access policy granting collection/index permissions to:
  - test compute host role
  - Lambda role (index updater)
  - ECS task role (runtime retrieval)
  - account root/admin role

**Why it exists**
- Isolates vector-store provisioning and policy logic from the rest of infra.

### `lib/aoss-update-stack.ts`
**Purpose**
- Deploys the Lambda that consumes SQS messages and writes documents to OpenSearch.

**Main resources**
- Docker-based Lambda (`lambda/aoss-update`) with SQS event source.
- Environment variables for OpenSearch host/index/region and secret name.
- Read access to processed text S3 bucket and OpenAI secret.

**Why it exists**
- Encapsulates document indexing mechanics and dependencies in one deployable unit.

### `lib/rag-app-stack.ts`
**Purpose**
- Deploys the RAG user-facing app on ECS Fargate.

**Main resources**
- ECS cluster, task definition, task execution role, CloudWatch log group.
- Container image built from `rag-app/`.
- Fargate service attached to ALB target group from base stack.

**Runtime inputs**
- `AWS_REGION`, `AOSS_INDEX_NAME`, `AOSS_ID`, `API_KEY_SECRET_NAME` passed to container.

**Why it exists**
- Separates app runtime deployment from foundational networking/auth/resources.

### `lib/test-compute-stack.ts`
**Purpose**
- Optional EC2 jump host stack for ad-hoc testing/inspection.

**Main resources**
- Ubuntu EC2 instance with user data setup (`git`, `awscli`, etc.).
- Broad IAM profile (includes AdministratorAccess).

**Notes**
- Useful for experiments, but very permissive; review before production use.

---

## 3) Lambda modules (`lambda/`)

### `lambda/pdf-processor/lambda_function.py`
**Purpose**
- Converts uploaded PDF files from knowledge-base bucket into text files in processed bucket.

**Trigger**
- S3 `ObjectCreated` events on the knowledge-base bucket.

**Flow**
1. Validate event format and bucket.
2. Download PDF with boto3.
3. Extract text page-by-page using `pypdf.PdfReader`.
4. Upload `.txt` object into processed bucket.

**Key env vars**
- `SOURCE_BUCKET_NAME`
- `DESTINATION_BUCKET_NAME`

### `lambda/aoss-trigger/app.py`
**Purpose**
- Bridge between processed-text S3 events and indexing queue.

**Trigger**
- S3 `ObjectCreated` events on processed bucket.

**Flow**
1. Validate incoming S3 event.
2. Build minimal message: `{ bucket, file }`.
3. Send message to SQS queue for indexing.

**Key env vars**
- `AOSS_UPDATE_QUEUE`
- `BUCKET_NAME`

### `lambda/aoss-update/app.py`
**Purpose**
- Consumes SQS messages and indexes document chunks into OpenSearch using LangChain.

**Trigger**
- SQS event source.

**Flow**
1. Optionally delete SQS message (manual delete logic in handler).
2. Parse message body and validate required fields.
3. Fetch OpenAI API key from Secrets Manager.
4. Load document from S3 using `S3FileLoader`.
5. Generate embeddings (`OpenAIEmbeddings`).
6. Write vectors/documents to OpenSearch (`OpenSearchVectorSearch.from_documents`).

**Key env vars**
- `QUEUE_URL`, `API_KEY_SECRET_NAME`
- `AOSS_ID`, `AOSS_INDEX_NAME`, `AOSS_AWS_REGION`

### `lambda/app-client-create-trigger/app.py`
**Purpose**
- Listens for Cognito app-client creation events (via EventBridge) and forwards details to SQS.

**Why**
- Enables asynchronous callback URL normalization flow for Cognito app client setup.

**Key env var**
- `TRIGGER_QUEUE`

### `lambda/call-back-url-init/app.py`
**Purpose**
- Consumes SQS payload and updates Cognito app client callback URL to lowercase ALB DNS.

**Trigger**
- SQS messages produced from app-client create flow.

**Why**
- Works around ALB DNS casing issue affecting Cognito callback handling.

**Key env vars**
- `USER_POOL_ID`, `APP_CLIENT_ID`, `ALB_DNS_NAME`, `SQS_QUEUE_URL`

### `lambda/call-back-url-update/app.py`
**Purpose**
- Handles Cognito `UpdateUserPoolClient` events to ensure callback URL remains normalized.

**Trigger**
- EventBridge rule on `UpdateUserPoolClient` CloudTrail events.

**Key env vars**
- `USER_POOL_ID`, `APP_CLIENT_ID`, `ALB_DNS_NAME`

---

## 4) RAG application modules (`rag-app/`)

### `rag-app/app.py`
**Purpose**
- Streamlit frontend for user chat and retrieval responses.

**Responsibilities**
- Initializes session state and per-user chat session IDs.
- Builds chain object (currently `bedrock_claude`) using `aoss_chat_bedrock.build_chain`.
- Handles user input and renders answer/source tabs.
- Maintains short conversation history for contextual follow-up.

**Key runtime dependencies**
- Expects OpenAI key in Streamlit secrets (`st.secrets["OPENAI_API_KEY"]`).
- Requires `AOSS_ID`, `AOSS_INDEX_NAME`, `AWS_REGION` env vars.

### `rag-app/aoss_chat_bedrock.py`
**Purpose**
- Core conversational retrieval logic for Bedrock + OpenSearch.

**Responsibilities**
- Creates `BedrockChat` LLM client.
- Creates OpenSearch retriever with SigV4 auth.
- Defines prompt templates (QA + standalone question condensation).
- Builds `ConversationalRetrievalChain` with source documents enabled.

**Other use**
- Can run as a CLI chat loop for local terminal interactions.

### `rag-app/helper_functions.py`
**Purpose**
- Shared helper for Secrets Manager access.

**Responsibilities**
- `get_secret_from_name(...)` fetches secret JSON or raw string.

### `rag-app/app_init.py`
**Purpose**
- Startup initializer for containerized Streamlit app.

**Responsibilities**
- Fetches OpenAI API key from Secrets Manager.
- Writes `/root/.streamlit/secrets.toml` consumed by Streamlit runtime.

### `rag-app/run_app.sh`
**Purpose**
- Entrypoint shell script for app container.

**Flow**
1. Run `app_init.py`.
2. Start Streamlit (`streamlit run app.py bedrock_claude`).

### `rag-app/Dockerfile`
**Purpose**
- Builds runtime image for Streamlit RAG app.

**Flow**
- Installs dependencies.
- Ensures `.streamlit` folder exists.
- Copies app code and executes `run_app.sh`.

### `rag-app/requirements.txt`
**Purpose**
- Direct, top-level dependencies (now pinned) for app development/runtime.

### `rag-app/requirements-lock.txt`
**Purpose**
- Full transitive lock snapshot (`pip freeze`) for reproducible CI/release installs.

---

## 5) Utility scripts (`scripts/`)

### `scripts/self-signed-cert-utility/self-signed-cert-utility.py`
**Purpose**
- Generates self-signed cert + key and uploads to IAM server certificates.

**Responsibilities**
- Parses cert subject config from JSON.
- Validates domain (`APP_DOMAIN` or default).
- Creates PEM files in local `.ssl` directory.
- Uploads cert to IAM using `IAM_SELF_SIGNED_SERVER_CERT_NAME`.

**Used by**
- ALB HTTPS listener setup in base infrastructure.

### `scripts/api-key-secret-manager-upload/api-key-secret-manager-upload.py`
**Purpose**
- CLI helper to securely upload OpenAI API key into Secrets Manager.

**Responsibilities**
- Supports profile-based or env-var-based AWS credentials.
- Reads secret value from hidden prompt (`getpass`).
- Updates specified secret name and validates response.

---

## 6) Container files in Lambda modules

### `lambda/aoss-update/Dockerfile`
**Purpose**
- Builds image-based Lambda for OpenSearch indexing function.

### `lambda/pdf-processor/Dockerfile`
**Purpose**
- Builds image-based Lambda for PDF text extraction function.

---

## 7) Config and dependency modules

### `package.json`
**Purpose**
- Node/CDK package manifest.

**Key scripts**
- `build`, `watch`, `test`, `cdk`.

### `cdk.json`
**Purpose**
- CDK app command and context feature flags.

### `tsconfig.json`
**Purpose**
- TypeScript compiler options for CDK project.

---

## 8) Tests

### `test/rag-with-amazon-bedrock-and-opensearch.test.ts`
**Purpose**
- Placeholder Jest test scaffold (currently mostly commented).

**Current state**
- Indicates intended CDK assertion testing pattern but not actively asserting stack outputs.

---

## 9) Data and docs directories

### `knowledgebase/`
- Local source PDFs staged for S3 deployment into the knowledge-base bucket.

### `architecture/`
- Architecture diagram assets used by README.

### `screenshots/`
- UI/console screenshots referenced in README walkthrough.

---

## 10) End-to-end module interaction map

1. PDF lands in knowledge-base S3 bucket.
2. `lambda/pdf-processor` converts PDF to TXT in processed bucket.
3. `lambda/aoss-trigger` sends `{bucket,file}` to SQS.
4. `lambda/aoss-update` loads TXT, embeds content, writes vectors to OpenSearch.
5. `rag-app` (Streamlit on ECS) queries OpenSearch retriever + Bedrock model to answer user questions.
6. ALB + Cognito provide HTTPS auth-protected access to the app.

---

## 11) Operational notes

- Region and certificate env vars are mandatory for successful infrastructure deployment.
- Secrets Manager is the trust boundary for the OpenAI API key used by both indexing and app layers.
- The optional test-compute stack is convenient for debugging but should be reviewed for security hardening in production.
