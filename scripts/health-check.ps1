param(
    [string]$Profile = "default",
    [string]$Region = "us-east-1",
    [string]$FileContains = "",
    [int]$LogMinutes = 30
)

$ErrorActionPreference = "Stop"
$aws = "C:\Program Files\Amazon\AWSCLIV2\aws.exe"

if (-not (Test-Path $aws)) {
    throw "AWS CLI not found at $aws"
}

$env:AWS_PAGER = ""

function Invoke-AwsJson {
    param(
        [string[]]$CmdArgs
    )
    $out = & $aws --profile $Profile --region $Region @CmdArgs --output json 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw ($out | Out-String)
    }
    return ($out | ConvertFrom-Json)
}

function Invoke-AwsText {
    param(
        [string[]]$CmdArgs
    )
    $out = & $aws --profile $Profile --region $Region @CmdArgs --output text 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw ($out | Out-String)
    }
    return ($out | Out-String).Trim()
}

function Add-Result {
    param(
        [string]$Check,
        [bool]$Ok,
        [string]$Details
    )
    $script:results += [pscustomobject]@{
        Check = $Check
        Status = if ($Ok) { "PASS" } else { "FAIL" }
        Details = $Details
    }
}

$results = @()
$allPass = $true

# 1) Identity
try {
    $who = Invoke-AwsJson -CmdArgs @("sts", "get-caller-identity")
    Add-Result -Check "AWS Identity" -Ok $true -Details "$($who.Account) / $($who.Arn)"
} catch {
    Add-Result -Check "AWS Identity" -Ok $false -Details $_.Exception.Message
    $allPass = $false
}

# 2) Core stack statuses
$stackNames = @("BaseInfraStack", "OpenSearchStack", "aossUpdateStack", "ragStack", "ragQueryStack")
foreach ($stack in $stackNames) {
    try {
        $status = Invoke-AwsText -CmdArgs @("cloudformation", "describe-stacks", "--stack-name", $stack, "--query", "Stacks[0].StackStatus")
        $ok = $status -in @("CREATE_COMPLETE", "UPDATE_COMPLETE", "IMPORT_COMPLETE")
        Add-Result -Check "Stack:$stack" -Ok $ok -Details $status
        if (-not $ok) { $allPass = $false }
    } catch {
        Add-Result -Check "Stack:$stack" -Ok $false -Details "Not found or inaccessible"
        $allPass = $false
    }
}

# 3) ECS app health
try {
    $clusterArn = Invoke-AwsText -CmdArgs @("ecs", "list-clusters", "--query", "clusterArns[?contains(@,'ragStack-ecsCluster')]|[0]")
    if (-not $clusterArn -or $clusterArn -eq "None") { throw "rag cluster not found" }

    $serviceArn = Invoke-AwsText -CmdArgs @("ecs", "list-services", "--cluster", $clusterArn, "--query", "serviceArns[?contains(@,'ragappservice')]|[0]")
    if (-not $serviceArn -or $serviceArn -eq "None") { throw "rag service not found" }

    $svc = Invoke-AwsJson -CmdArgs @("ecs", "describe-services", "--cluster", $clusterArn, "--services", $serviceArn)
    $service = $svc.services[0]
    $running = [int]$service.runningCount
    $desired = [int]$service.desiredCount
    $taskDef = $service.taskDefinition
    $ok = ($running -ge [Math]::Min(1, $desired))
    Add-Result -Check "ECS Service" -Ok $ok -Details "running=$running desired=$desired taskDef=$taskDef"
    if (-not $ok) { $allPass = $false }
} catch {
    Add-Result -Check "ECS Service" -Ok $false -Details $_.Exception.Message
    $allPass = $false
}

# 4) Buckets + optional file propagation check
try {
    $pdfFn = Invoke-AwsText -CmdArgs @("lambda", "list-functions", "--query", "Functions[?contains(FunctionName,'pdfProcessorFn')].FunctionName|[0]")
    if (-not $pdfFn -or $pdfFn -eq "None") { throw "pdfProcessor function not found" }

    $envVars = Invoke-AwsJson -CmdArgs @("lambda", "get-function-configuration", "--function-name", $pdfFn, "--query", "Environment.Variables")
    $src = $envVars.SOURCE_BUCKET_NAME
    $dst = $envVars.DESTINATION_BUCKET_NAME

    Add-Result -Check "Pipeline Buckets" -Ok $true -Details "source=$src processed=$dst"

    if ($FileContains) {
        $srcObjs = Invoke-AwsJson -CmdArgs @("s3api", "list-objects-v2", "--bucket", $src)
        $dstObjs = Invoke-AwsJson -CmdArgs @("s3api", "list-objects-v2", "--bucket", $dst)

        $srcMatch = @($srcObjs.Contents | Where-Object { $_.Key -like "*$FileContains*" } | Sort-Object LastModified -Descending | Select-Object -First 1)
        $dstMatch = @($dstObjs.Contents | Where-Object { $_.Key -like "*$FileContains*" } | Sort-Object LastModified -Descending | Select-Object -First 1)

        $srcOk = $srcMatch.Count -gt 0
        $dstOk = $dstMatch.Count -gt 0

        Add-Result -Check "Source Object Match" -Ok $srcOk -Details $(if ($srcOk) { "$($srcMatch[0].Key) @ $($srcMatch[0].LastModified)" } else { "No match for '$FileContains'" })
        Add-Result -Check "Processed Object Match" -Ok $dstOk -Details $(if ($dstOk) { "$($dstMatch[0].Key) @ $($dstMatch[0].LastModified)" } else { "No match for '$FileContains'" })

        if (-not $srcOk -or -not $dstOk) { $allPass = $false }
    }
} catch {
    Add-Result -Check "Pipeline Buckets" -Ok $false -Details $_.Exception.Message
    $allPass = $false
}

# 5) aoss-update recent log scan
try {
    $aossFn = Invoke-AwsText -CmdArgs @("lambda", "list-functions", "--query", "Functions[?contains(FunctionName,'aossUpdate')].FunctionName|[0]")
    if (-not $aossFn -or $aossFn -eq "None") { throw "aossUpdate function not found" }

    $logText = & $aws --profile $Profile --region $Region logs tail "/aws/lambda/$aossFn" --since "$($LogMinutes)m" --format short 2>$null | Out-String
    $errorHits = @([regex]::Matches($logText, "(?im)\b(ERROR|Exception|Traceback)\b")).Count
    $ok = $errorHits -eq 0
    Add-Result -Check "aoss-update Logs" -Ok $ok -Details "errorsInLast${LogMinutes}m=$errorHits"
    if (-not $ok) { $allPass = $false }
} catch {
    Add-Result -Check "aoss-update Logs" -Ok $false -Details $_.Exception.Message
    $allPass = $false
}

Write-Host ""
Write-Host "Health Check Summary" -ForegroundColor Cyan
$results | Format-Table -AutoSize

if ($allPass) {
    Write-Host "\nOverall: PASS" -ForegroundColor Green
    exit 0
}

Write-Host "\nOverall: FAIL" -ForegroundColor Red
exit 1
