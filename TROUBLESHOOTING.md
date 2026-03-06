# Troubleshooting

This file captures common issues seen during deployment and first run.

## 1) AWS session expired

Symptoms:
- `Your session has expired. Please reauthenticate using 'aws login'.`
- Stack/resource checks return access or not-found style errors.

Fix:

```powershell
aws --profile default login
aws --profile default sts get-caller-identity --output json
```

Then rerun deploy:

```powershell
.\scripts\deploy-dev.ps1 -Profile default
```

## 2) `Failed to export AWS credentials for profile 'default'`

Symptoms:
- Deployment script fails early in `Set-AwsCredentialEnv`.

Fix:
- The scripts were updated to support:
  - static credentials (no session token)
  - SSO export fallback
  - profile fallback via `AWS_PROFILE`

Use:

```powershell
.\scripts\deploy-dev.ps1 -Profile default
```

## 3) `index_not_found_exception` for `rag-oai-index`

Symptoms:
- App query fails with OpenSearch 404 and missing index.

Cause:
- No document had been successfully indexed yet.

Fix checklist:
1. Upload at least one PDF to the knowledgebase bucket.
2. Confirm `pdf-processor` creates a `.txt` in processed bucket.
3. Confirm `aoss-trigger` sends to `AOSS_Update_Queue`.
4. Confirm `aoss-update` Lambda log shows successful `_bulk` write.

## 4) `aoss-update` Lambda failed with spaCy write error

Symptoms:
- Runtime error trying to install `en_core_web_sm` in Lambda read-only path.

Fix:
- `lambda/aoss-update/app.py` now reads processed `.txt` directly from S3.
- `lambda/aoss-update/requirements.txt` no longer depends on `unstructured`.

## 5) `AccessDeniedException` for `bedrock:InvokeModel` in `aoss-update`

Symptoms:
- Indexer logs show access denied invoking Titan embeddings model.

Fix:
- Added `bedrock:InvokeModel` to `aossUpdateRole` in `lib/base-infra-stack.ts`.
- Redeploy:

```powershell
.\scripts\deploy-dev.ps1 -Profile default -SkipLogin
```

## 6) App URL and cert warning

Symptoms:
- Browser warns on certificate.

Cause:
- This deployment uses a self-signed certificate for ALB HTTPS.

Fix:
- Proceed through browser warning for dev/test, or replace with ACM-managed cert for production.
