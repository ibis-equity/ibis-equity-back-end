param(
    [string]$AccountId = "908027375670",
    [string]$Region = "us-east-1",
    [string]$CertName = "rag-dev-cert-20260304",
    [string]$IbisSiteOrigin = "http://localhost:4200",
    [switch]$SkipLogin
)

$ErrorActionPreference = "Stop"

$awsExe = "C:\Program Files\Amazon\AWSCLIV2\aws.exe"
if (-not (Test-Path $awsExe)) {
    throw "AWS CLI not found at $awsExe"
}

if (-not $SkipLogin) {
    Write-Host "Refreshing AWS login session..." -ForegroundColor Yellow
    & $awsExe login
}

Write-Host "Validating AWS identity..." -ForegroundColor Cyan
& $awsExe sts get-caller-identity | Out-Host

$env:CDK_DEFAULT_ACCOUNT = $AccountId
$env:CDK_DEFAULT_REGION = $Region
$env:IAM_SELF_SIGNED_SERVER_CERT_NAME = $CertName
$env:IBIS_SITE_ORIGIN = $IbisSiteOrigin

Write-Host "Checking if ragQueryStack exists..." -ForegroundColor Cyan
$stackExists = $true
$null = & $awsExe cloudformation describe-stacks --stack-name ragQueryStack --region $Region 2>$null
if ($LASTEXITCODE -ne 0) {
    $stackExists = $false
}

if (-not $stackExists) {
    Write-Host "ragQueryStack not found. Deploying now..." -ForegroundColor Yellow
    npx cdk deploy ragQueryStack --require-approval never
}

Write-Host "Fetching RagQueryApiUrl output..." -ForegroundColor Cyan
$ragUrl = & $awsExe cloudformation describe-stacks --stack-name ragQueryStack --region $Region --query "Stacks[0].Outputs[?OutputKey=='RagQueryApiUrl'].OutputValue" --output text

if (-not $ragUrl) {
    throw "RagQueryApiUrl output not found on ragQueryStack."
}

Write-Host "RagQueryApiUrl:" -ForegroundColor Green
Write-Host $ragUrl -ForegroundColor Green
