[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PdfPath,

    [string]$Region = "us-east-1",
    [string]$BaseInfraStackName = "BaseInfraStack",
    [string]$AossUpdateStackName = "aossUpdateStack",
    [string]$IndexName = "rag-oai-index",
    [string]$CollectionId,
    [int]$TimeoutSeconds = 300,
    [int]$PollIntervalSeconds = 5
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
$env:AWS_PAGER = ""

function Write-Step {
    param([string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Invoke-AwsJson {
    param([string[]]$AwsArgs)
    $raw = aws @AwsArgs 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "AWS CLI failed: aws $($AwsArgs -join ' ')`n$raw"
    }
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return $null
    }
    return ($raw | ConvertFrom-Json)
}

function Invoke-AwsText {
    param([string[]]$AwsArgs)
    $raw = aws @AwsArgs 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "AWS CLI failed: aws $($AwsArgs -join ' ')`n$raw"
    }
    return ($raw | Out-String).Trim()
}

function Resolve-Buckets {
    param([string]$StackName)

    $res = Invoke-AwsJson @(
        "cloudformation", "describe-stack-resources",
        "--stack-name", $StackName,
        "--query", "StackResources[?ResourceType=='AWS::S3::Bucket'].{LogicalId:LogicalResourceId,PhysicalId:PhysicalResourceId}",
        "--output", "json",
        "--region", $Region
    )

    if (-not $res) {
        throw "No S3 buckets found in stack $StackName"
    }

    $knowledge = $res | Where-Object { $_.LogicalId -match "knowledgeBase" } | Select-Object -First 1
    $processed = $res | Where-Object { $_.LogicalId -match "processedText" } | Select-Object -First 1

    if (-not $knowledge) {
        throw "Could not resolve knowledge bucket in stack $StackName"
    }
    if (-not $processed) {
        throw "Could not resolve processed bucket in stack $StackName"
    }

    return [PSCustomObject]@{
        KnowledgeBucket = $knowledge.PhysicalId
        ProcessedBucket = $processed.PhysicalId
    }
}

function Resolve-LambdaName {
    param(
        [string]$StackName,
        [string]$LogicalPattern
    )

    $res = Invoke-AwsJson @(
        "cloudformation", "describe-stack-resources",
        "--stack-name", $StackName,
        "--query", "StackResources[?ResourceType=='AWS::Lambda::Function'].{LogicalId:LogicalResourceId,PhysicalId:PhysicalResourceId}",
        "--output", "json",
        "--region", $Region
    )

    if (-not $res) {
        throw "No Lambda functions found in stack $StackName"
    }

    $match = $res | Where-Object { $_.LogicalId -match $LogicalPattern } | Select-Object -First 1
    if (-not $match) {
        throw "Could not resolve lambda matching '$LogicalPattern' in stack $StackName"
    }

    return $match.PhysicalId
}

function Resolve-CollectionId {
    if ($CollectionId) {
        return $CollectionId
    }
    if ($env:AOSS_ID) {
        return $env:AOSS_ID
    }

    $collections = Invoke-AwsJson @(
        "opensearchserverless", "list-collections",
        "--query", "collectionSummaries[*].id",
        "--output", "json",
        "--region", $Region
    )

    if (-not $collections) {
        throw "No OpenSearch Serverless collections found in region $Region"
    }

    $collectionList = @($collections)

    if ($collectionList.Count -eq 1) {
        return $collectionList[0]
    }

    throw "Multiple collections found. Pass -CollectionId explicitly. Found: $($collectionList -join ', ')"
}

function Wait-ForProcessedText {
    param(
        [string]$Bucket,
        [string]$Key,
        [int]$MaxSeconds,
        [int]$IntervalSeconds
    )

    $elapsed = 0
    while ($elapsed -lt $MaxSeconds) {
        $head = aws s3api head-object --bucket $Bucket --key $Key --region $Region 2>$null
        if ($LASTEXITCODE -eq 0) {
            return $true
        }
        Start-Sleep -Seconds $IntervalSeconds
        $elapsed += $IntervalSeconds
    }
    return $false
}

function Wait-ForIndexedDocument {
    param(
        [string]$AossHost,
        [string]$IdxName,
        [string]$SourceUri,
        [int]$MaxSeconds,
        [int]$IntervalSeconds
    )

    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    $pythonExe = if (Test-Path $venvPython) { $venvPython } else { "python" }

    $queryScript = "import json,boto3; from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth; region='$Region'; aoss_host='$AossHost'; index_name='$IdxName'; source_uri='$SourceUri'; auth=AWSV4SignerAuth(boto3.Session().get_credentials(), region, 'aoss'); client=OpenSearch(hosts=[{'host':aoss_host,'port':443}], http_auth=auth, use_ssl=True, verify_certs=True, connection_class=RequestsHttpConnection); resp=client.search(index=index_name, body={'query':{'term':{'metadata.source.keyword':source_uri}}}); count=resp.get('hits',{}).get('total',{}).get('value',0); print(json.dumps({'count':count}))"

    $elapsed = 0
    while ($elapsed -lt $MaxSeconds) {
        $result = & $pythonExe -c $queryScript 2>&1
        if ($LASTEXITCODE -eq 0) {
            try {
                $obj = $result | ConvertFrom-Json
                if ($obj.count -ge 1) {
                    return $true
                }
            } catch {
                # keep polling on transient parse errors
            }
        }
        Start-Sleep -Seconds $IntervalSeconds
        $elapsed += $IntervalSeconds
    }
    return $false
}

if (-not (Test-Path $PdfPath)) {
    throw "PDF file not found: $PdfPath"
}

Write-Step "Resolving infrastructure resources"
$buckets = Resolve-Buckets -StackName $BaseInfraStackName
$pdfLambda = Resolve-LambdaName -StackName $BaseInfraStackName -LogicalPattern "pdfProcessor"
$triggerLambda = Resolve-LambdaName -StackName $BaseInfraStackName -LogicalPattern "aossTrigger"
$updateLambda = Resolve-LambdaName -StackName $AossUpdateStackName -LogicalPattern "aossUpdate"
$collection = Resolve-CollectionId

    $aossHost = "$collection.$Region.aoss.amazonaws.com"

$baseName = [System.IO.Path]::GetFileNameWithoutExtension($PdfPath)
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$pdfKey = "$baseName-$timestamp.pdf"
$txtKey = "$baseName-$timestamp.txt"
$indexedSource = "s3://$($buckets.ProcessedBucket)/$txtKey"

Write-Step "Uploading PDF to knowledge bucket (Stage 1)"
aws s3 cp $PdfPath "s3://$($buckets.KnowledgeBucket)/$pdfKey" --region $Region | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Upload failed"
}

$stage1Head = aws s3api head-object --bucket $buckets.KnowledgeBucket --key $pdfKey --region $Region 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "Stage 1 verification failed. Could not read uploaded object. Details: $stage1Head"
}
Write-Host "Stage 1 PASS: s3://$($buckets.KnowledgeBucket)/$pdfKey" -ForegroundColor Green

Write-Step "Waiting for processed text in processed bucket (Stage 2)"
$stage2Ok = Wait-ForProcessedText -Bucket $buckets.ProcessedBucket -Key $txtKey -MaxSeconds $TimeoutSeconds -IntervalSeconds $PollIntervalSeconds
if (-not $stage2Ok) {
    Write-Host "Stage 2 FAIL: Processed text not found within timeout." -ForegroundColor Red
    Write-Host "Recent logs:" -ForegroundColor Yellow
    aws logs tail "/aws/lambda/$pdfLambda" --since 10m --region $Region
    aws logs tail "/aws/lambda/$triggerLambda" --since 10m --region $Region
    throw "Stopping after Stage 2 timeout"
}
Write-Host "Stage 2 PASS: s3://$($buckets.ProcessedBucket)/$txtKey" -ForegroundColor Green

Write-Step "Waiting for document in OpenSearch index (Stage 3)"
    $stage3Ok = Wait-ForIndexedDocument -AossHost $aossHost -IdxName $IndexName -SourceUri $indexedSource -MaxSeconds $TimeoutSeconds -IntervalSeconds $PollIntervalSeconds
if (-not $stage3Ok) {
    Write-Host "Stage 3 FAIL: Document not found in index within timeout." -ForegroundColor Red
    Write-Host "Recent logs:" -ForegroundColor Yellow
    aws logs tail "/aws/lambda/$triggerLambda" --since 10m --region $Region
    aws logs tail "/aws/lambda/$updateLambda" --since 10m --region $Region
    throw "Stopping after Stage 3 timeout"
}
Write-Host "Stage 3 PASS: Indexed source $indexedSource" -ForegroundColor Green

Write-Step "Done"
[PSCustomObject]@{
    UploadedPdf = "s3://$($buckets.KnowledgeBucket)/$pdfKey"
    ProcessedText = "s3://$($buckets.ProcessedBucket)/$txtKey"
    IndexedSource = $indexedSource
    IndexName = $IndexName
    CollectionHost = $aossHost
} | Format-List
