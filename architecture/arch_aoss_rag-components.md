# Architecture Components: arch_aoss_rag.png

This document describes each component shown in [arch_aoss_rag.png](./arch_aoss_rag.png), including its name, location, purpose, and function.

## Component Catalog

| Name | Diagram Location | Implementation Location | Purpose | Function |
|---|---|---|---|---|
| User | Far left | External actor (not in repo) | Represents the application consumer | Sends requests to the app, authenticates, asks questions, and receives responses |
| Application Load Balancer | Left, between User and app/auth | [lib/base-infra-stack.ts](../lib/base-infra-stack.ts) | Public ingress for the application | Terminates inbound traffic and routes authenticated sessions to the ECS service |
| Cognito Login UI | Upper-left | [lib/base-infra-stack.ts](../lib/base-infra-stack.ts) | User authentication and identity management | Handles login flow and returns authenticated identity/session context |
| ECS Fargate Service (RAG app) | Lower-left | [rag-app/app.py](../rag-app/app.py), [rag-app/aoss_chat_bedrock.py](../rag-app/aoss_chat_bedrock.py), [lib/rag-stack.ts](../lib/rag-stack.ts) | Host the interactive RAG application | Accepts user prompts, builds retrieval + generation calls, renders chat UI |
| Amazon OpenSearch Service Index | Mid-left center | [lib/opensearch-stack.ts](../lib/opensearch-stack.ts), [lambda/aoss-update/app.py](../lambda/aoss-update/app.py) | Vector store for semantic retrieval | Stores embedded document chunks and serves nearest-neighbor retrieval for RAG |
| Amazon Bedrock | Bottom-center left | [lambda/rag-query/app.py](../lambda/rag-query/app.py), [lambda/aoss-update/app.py](../lambda/aoss-update/app.py), [rag-app/aoss_chat_bedrock.py](../rag-app/aoss_chat_bedrock.py) | Managed model and embedding APIs | Produces embeddings for indexing and model responses for user questions |
| CDK S3 Bucket Deployment | Top-center right | [lib/base-infra-stack.ts](../lib/base-infra-stack.ts), local source folder [knowledgebase](../knowledgebase) | Seed initial documents into S3 via deployment | Copies local PDFs from repo into the knowledgebase bucket during CDK deploy |
| Knowledgebase S3 Bucket | Upper-right | [lib/base-infra-stack.ts](../lib/base-infra-stack.ts) | Primary landing zone for original PDFs | Stores uploaded PDFs and emits ObjectCreated events for processing |
| Lambda: pdf-processor | Upper-right, right of knowledge bucket | [lambda/pdf-processor/lambda_function.py](../lambda/pdf-processor/lambda_function.py) | Convert uploaded PDFs into plain text | Reads PDF from knowledge bucket, extracts text, writes .txt to processed bucket |
| Processed Text S3 Bucket | Mid-right | [lib/base-infra-stack.ts](../lib/base-infra-stack.ts) | Store extraction output artifacts | Holds text files that trigger downstream queueing/indexing flow |
| Lambda: aoss-trigger | Mid-right, left of processed bucket | [lambda/aoss-trigger/app.py](../lambda/aoss-trigger/app.py) | Bridge S3 processed events to queue jobs | On .txt upload, sends message containing bucket/key metadata to SQS |
| SQS AOSS_Update_Queue | Lower-right | [lib/base-infra-stack.ts](../lib/base-infra-stack.ts) | Decouple extraction and indexing workloads | Buffers indexing jobs, smooths burst traffic, and triggers indexing Lambda |
| Lambda: aoss-update | Lower center | [lambda/aoss-update/app.py](../lambda/aoss-update/app.py), [lib/aoss-update-stack.ts](../lib/aoss-update-stack.ts) | Index processed documents into OpenSearch | Reads queued .txt reference, creates embeddings, writes vectors/doc metadata |

## End-to-End Flow Summary

1. A PDF is uploaded to the Knowledgebase S3 Bucket.
2. `pdf-processor` Lambda extracts text and writes a `.txt` file to Processed Text S3 Bucket.
3. `aoss-trigger` Lambda sends an indexing message to SQS with bucket/key details.
4. `aoss-update` Lambda consumes the message, creates embeddings, and indexes vectors in OpenSearch.
5. ECS RAG app queries OpenSearch for relevant context and uses Bedrock for final responses.

## Notes on Location Terminology

- Diagram Location: where the component is visually placed in [arch_aoss_rag.png](./arch_aoss_rag.png).
- Implementation Location: where it is defined in this repository (CDK, Lambda, or app code).
