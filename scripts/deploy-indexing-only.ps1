param(
    [string]$AccountId = "",
    [string]$Region = "us-east-1",
    [string]$CertName = "rag-dev-cert-20260304",
    [switch]$SkipLogin
)

$ErrorActionPreference = "Stop"

Write-Host "Preparing indexing-only deployment (OpenSearchStack + aossUpdateStack)..." -ForegroundColor Cyan

$awsExe = "C:\Program Files\Amazon\AWSCLIV2\aws.exe"
if (-not (Test-Path $awsExe)) {
    throw "AWS CLI not found at $awsExe"
}

function Ensure-AwsSession {
    param([string]$CliPath, [bool]$TryLogin)
    try {
        $caller = & $CliPath sts get-caller-identity --output json | ConvertFrom-Json
        return $caller
    }
    catch {
        if (-not $TryLogin) {
            throw "AWS session is invalid and -SkipLogin was provided. Run aws login first."
        }
        Write-Host "AWS session invalid/expired. Running aws login..." -ForegroundColor Yellow
        & $CliPath login
        $caller = & $CliPath sts get-caller-identity --output json | ConvertFrom-Json
        return $caller
    }
}

function Ensure-Docker {
    $null = docker version
}

Write-Host "Validating AWS identity..." -ForegroundColor Cyan
$callerIdentity = Ensure-AwsSession -CliPath $awsExe -TryLogin (-not $SkipLogin)
$callerIdentity | ConvertTo-Json | Out-Host

if (-not $AccountId) {
    $AccountId = $callerIdentity.Account
}

Write-Host "Checking Docker engine..." -ForegroundColor Cyan
Ensure-Docker

$env:CDK_DEFAULT_ACCOUNT = $AccountId
$env:CDK_DEFAULT_REGION = $Region
$env:IAM_SELF_SIGNED_SERVER_CERT_NAME = $CertName

Write-Host "Deploying OpenSearchStack..." -ForegroundColor Cyan
npx cdk deploy OpenSearchStack --require-approval never

Write-Host "Deploying aossUpdateStack..." -ForegroundColor Cyan
npx cdk deploy aossUpdateStack --require-approval never

Write-Host "Indexing-only deployment completed." -ForegroundColor Green
