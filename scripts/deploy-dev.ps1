param(
    [string]$AccountId = "",
    [string]$Region = "us-east-1",
    [string]$CertName = "rag-dev-cert-20260304",
    [string]$IbisSiteOrigin = "http://localhost:4200",
    [string]$Profile = "default",
    [switch]$IncludeRagQuery,
    [switch]$SkipLogin
)

$ErrorActionPreference = "Stop"

# Resolve repo root based on script location so this script works from any cwd.
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..")
$cdkJsonPath = Join-Path $repoRoot "cdk.json"
if (-not (Test-Path $cdkJsonPath)) {
    throw "Could not find cdk.json at expected repo root: $repoRoot"
}

Write-Host "Preparing dev deployment..." -ForegroundColor Cyan

$awsExe = "C:\Program Files\Amazon\AWSCLIV2\aws.exe"
if (-not (Test-Path $awsExe)) {
    throw "AWS CLI not found at $awsExe"
}

function Ensure-AwsSession {
    param([string]$CliPath, [bool]$TryLogin, [string]$AwsProfile)

    $profileArgs = @()
    if ($AwsProfile) {
        $profileArgs = @("--profile", $AwsProfile)
    }

    try {
        $caller = & $CliPath @profileArgs sts get-caller-identity --output json | ConvertFrom-Json
        return $caller
    }
    catch {
        if (-not $TryLogin) {
            throw "AWS session is invalid and -SkipLogin was provided. Run aws login first."
        }
        Write-Host "AWS session invalid/expired. Running aws login..." -ForegroundColor Yellow
        & $CliPath @profileArgs login
        $caller = & $CliPath @profileArgs sts get-caller-identity --output json | ConvertFrom-Json
        return $caller
    }
}

function Ensure-Docker {
    $null = docker version
}

function Invoke-CheckedCommand {
    param(
        [string]$Command,
        [string[]]$Arguments,
        [string]$FriendlyName
    )

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FriendlyName failed with exit code $LASTEXITCODE"
    }
}

function Set-AwsCredentialEnv {
    param([string]$CliPath, [string]$AwsProfile, [bool]$TryLogin)

    $profileArgs = @()
    if ($AwsProfile) {
        $profileArgs = @("--profile", $AwsProfile)
    }

    $credJson = $null
    try {
        $exportRaw = & $CliPath @profileArgs configure export-credentials --format process 2>$null
        if ($LASTEXITCODE -eq 0 -and $exportRaw) {
            $credJson = $exportRaw | ConvertFrom-Json
        }
    }
    catch {
        $credJson = $null
    }

    if ($TryLogin -and $credJson.Expiration) {
        $expiryUtc = ([datetime]$credJson.Expiration).ToUniversalTime()
        $nowUtc = (Get-Date).ToUniversalTime()
        # Refresh if token may expire during a long container build/deploy.
        if ($expiryUtc -le $nowUtc.AddMinutes(60)) {
            Write-Host "AWS credentials expire soon ($($credJson.Expiration)); refreshing login..." -ForegroundColor Yellow
            & $CliPath @profileArgs login
            $credJson = & $CliPath @profileArgs configure export-credentials --format process | ConvertFrom-Json
        }
    }

    # Fallback for static-key profiles where export-credentials may not provide values.
    if (-not $credJson -or -not $credJson.AccessKeyId -or -not $credJson.SecretAccessKey) {
        $accessKeyRaw = & $CliPath @profileArgs configure get aws_access_key_id 2>$null
        $secretKeyRaw = & $CliPath @profileArgs configure get aws_secret_access_key 2>$null
        $sessionTokenRaw = & $CliPath @profileArgs configure get aws_session_token 2>$null

        $accessKey = if ($accessKeyRaw) { "$accessKeyRaw".Trim() } else { "" }
        $secretKey = if ($secretKeyRaw) { "$secretKeyRaw".Trim() } else { "" }
        $sessionToken = if ($sessionTokenRaw) { "$sessionTokenRaw".Trim() } else { "" }

        if ($accessKey -and $secretKey) {
            $env:AWS_ACCESS_KEY_ID = $accessKey
            $env:AWS_SECRET_ACCESS_KEY = $secretKey
            if ($sessionToken) {
                $env:AWS_SESSION_TOKEN = $sessionToken
            }
            elseif (Test-Path Env:AWS_SESSION_TOKEN) {
                Remove-Item Env:AWS_SESSION_TOKEN
            }
            return $true
        }

        # Last resort: keep the profile for SDK/CLI resolution.
        if ($AwsProfile) {
            Write-Host "Could not export explicit credentials; falling back to AWS_PROFILE='$AwsProfile'." -ForegroundColor Yellow
            $env:AWS_PROFILE = $AwsProfile
            return $false
        }

        throw "Failed to export AWS credentials for profile '$AwsProfile'."
    }

    $env:AWS_ACCESS_KEY_ID = $credJson.AccessKeyId
    $env:AWS_SECRET_ACCESS_KEY = $credJson.SecretAccessKey
    if ($credJson.SessionToken) {
        $env:AWS_SESSION_TOKEN = $credJson.SessionToken
    }
    elseif (Test-Path Env:AWS_SESSION_TOKEN) {
        Remove-Item Env:AWS_SESSION_TOKEN
    }

    return $true
}

function Assert-StackComplete {
    param([string]$CliPath, [string]$StackName)

    function Get-StackStatus {
        param([string]$Name)
        return (& $CliPath cloudformation describe-stacks --stack-name $Name --query "Stacks[0].StackStatus" --output text).Trim()
    }

    try {
        $status = Get-StackStatus -Name $StackName
    }
    catch {
        throw "Could not find CloudFormation stack '$StackName' after deploy."
    }

    if ($status -like "*_IN_PROGRESS") {
        Write-Host "Waiting for $StackName to finish (current: $status)..." -ForegroundColor Yellow
        $waitExitCode = 0

        & $CliPath cloudformation wait stack-create-complete --stack-name $StackName
        $waitExitCode = $LASTEXITCODE

        if ($waitExitCode -ne 0) {
            & $CliPath cloudformation wait stack-update-complete --stack-name $StackName
            $waitExitCode = $LASTEXITCODE
        }

        if ($waitExitCode -ne 0) {
            throw "Stack '$StackName' did not reach a complete status after waiting."
        }

        $status = Get-StackStatus -Name $StackName
    }

    $validStatuses = @("CREATE_COMPLETE", "UPDATE_COMPLETE", "IMPORT_COMPLETE")
    if ($status -notin $validStatuses) {
        throw "Stack '$StackName' finished in unexpected status: $status"
    }

    Write-Host "Verified $StackName status: $status" -ForegroundColor Green
}

Write-Host "Validating AWS identity..." -ForegroundColor Cyan
$callerIdentity = Ensure-AwsSession -CliPath $awsExe -TryLogin (-not $SkipLogin) -AwsProfile $Profile
$callerIdentity | ConvertTo-Json | Out-Host

if (-not $AccountId) {
    $AccountId = $callerIdentity.Account
}

Write-Host "Checking Docker engine..." -ForegroundColor Cyan
Ensure-Docker

$env:CDK_DEFAULT_ACCOUNT = $AccountId
$env:CDK_DEFAULT_REGION = $Region
$env:IAM_SELF_SIGNED_SERVER_CERT_NAME = $CertName
$env:IBIS_SITE_ORIGIN = $IbisSiteOrigin
$env:AWS_SDK_LOAD_CONFIG = "1"

Write-Host "Exporting AWS credentials for CDK..." -ForegroundColor Cyan
 $usingExplicitCreds = Set-AwsCredentialEnv -CliPath $awsExe -AwsProfile $Profile -TryLogin (-not $SkipLogin)
# Use explicit env credentials for CDK to avoid profile-chain resolution issues.
if ($usingExplicitCreds -and (Test-Path Env:AWS_PROFILE)) {
    Remove-Item Env:AWS_PROFILE
}

# Avoid AWS CLI pager blocking output in non-interactive runs.
$env:AWS_PAGER = ""

Push-Location $repoRoot
try {
    $targetStacks = @(
        "BaseInfraStack",
        "TestComputeStack",
        "OpenSearchStack",
        "aossUpdateStack",
        "ragStack"
    )

    if ($IncludeRagQuery) {
        $targetStacks += "ragQueryStack"
    }

    $deployArgs = @("cdk", "deploy") + $targetStacks + @("--require-approval", "never")

    Write-Host "Deploying CDK stacks for dev environment: $($targetStacks -join ', ')" -ForegroundColor Cyan
    Invoke-CheckedCommand -Command "npx" -Arguments $deployArgs -FriendlyName "CDK deploy"
}
finally {
    Pop-Location
}

Write-Host "Verifying deployed stacks in CloudFormation..." -ForegroundColor Cyan
Assert-StackComplete -CliPath $awsExe -StackName "BaseInfraStack"
Assert-StackComplete -CliPath $awsExe -StackName "TestComputeStack"
Assert-StackComplete -CliPath $awsExe -StackName "OpenSearchStack"
Assert-StackComplete -CliPath $awsExe -StackName "aossUpdateStack"
Assert-StackComplete -CliPath $awsExe -StackName "ragStack"

if ($IncludeRagQuery) {
    Assert-StackComplete -CliPath $awsExe -StackName "ragQueryStack"
}

Write-Host "Dev deployment command completed." -ForegroundColor Green
