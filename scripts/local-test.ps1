param(
    [ValidateSet("lambda", "streamlit", "both")]
    [string]$Mode = "lambda",
    [Parameter(Mandatory = $true)]
    [string]$AossId,
    [Parameter(Mandatory = $true)]
    [string]$AossIndexName,
    [string]$Region = "us-east-1",
    [string]$Question = "What is this knowledge base about?",
    [string]$PollyVoiceId = "Joanna",
    [string]$PollyEngine = "standard",
    [string]$PollyLanguageCode = "en-US",
    [switch]$InstallDeps
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Set-SharedEnv {
    param(
        [string]$CollectionId,
        [string]$IndexName,
        [string]$AwsRegion,
        [string]$VoiceId,
        [string]$Engine,
        [string]$LanguageCode
    )

    $env:AWS_REGION = $AwsRegion
    $env:AOSS_AWS_REGION = $AwsRegion
    $env:AOSS_ID = $CollectionId
    $env:AOSS_INDEX_NAME = $IndexName

    $env:ENABLE_POLLY_TTS = "true"
    $env:RAG_QUERY_ENABLE_POLLY = "true"
    $env:POLLY_VOICE_ID = $VoiceId
    $env:POLLY_ENGINE = $Engine
    $env:POLLY_LANGUAGE_CODE = $LanguageCode
}

function Test-LambdaLocal {
    param(
        [string]$RootPath,
        [string]$Prompt,
        [switch]$InstallPackages
    )

    Write-Host "Running local rag-query Lambda test..." -ForegroundColor Cyan
    Push-Location (Join-Path $RootPath "lambda\rag-query")
    try {
        if ($InstallPackages) {
            python -m pip install -r requirements.txt
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to install lambda dependencies."
            }
        }

        $pythonCode = @"
import json
import app

ev = {
    "body": json.dumps(
        {
            "question": "$Prompt",
            "config": {
                "speechEnabled": True,
                "voiceId": "$PollyVoiceId",
                "engine": "$PollyEngine",
                "languageCode": "$PollyLanguageCode"
            }
        }
    )
}
res = app.lambda_handler(ev, None)
body = json.loads(res["body"])
print("status=", res.get("statusCode"))
print("answer_preview=", (body.get("answer") or "")[:160])
print("speech_enabled=", (body.get("speech") or {}).get("enabled"))
print("has_audio=", bool((body.get("speech") or {}).get("audioBase64")))
"@

        $pythonCode | python -
        if ($LASTEXITCODE -ne 0) {
            throw "Lambda local test failed."
        }
    }
    finally {
        Pop-Location
    }
}

function Start-StreamlitLocal {
    param(
        [string]$RootPath,
        [switch]$InstallPackages
    )

    Write-Host "Starting local Streamlit app..." -ForegroundColor Cyan
    Push-Location (Join-Path $RootPath "rag-app")
    try {
        if ($InstallPackages) {
            python -m pip install -r requirements.txt
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to install rag-app dependencies."
            }
        }

        python app_init.py
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to initialize Streamlit app secrets."
        }

        Write-Host "Open http://localhost:8501 after startup." -ForegroundColor Green
        streamlit run app.py bedrock_claude
    }
    finally {
        Pop-Location
    }
}

Set-SharedEnv -CollectionId $AossId -IndexName $AossIndexName -AwsRegion $Region -VoiceId $PollyVoiceId -Engine $PollyEngine -LanguageCode $PollyLanguageCode

switch ($Mode) {
    "lambda" {
        Test-LambdaLocal -RootPath $repoRoot -Prompt $Question -InstallPackages:$InstallDeps
    }
    "streamlit" {
        Start-StreamlitLocal -RootPath $repoRoot -InstallPackages:$InstallDeps
    }
    "both" {
        Test-LambdaLocal -RootPath $repoRoot -Prompt $Question -InstallPackages:$InstallDeps
        Start-StreamlitLocal -RootPath $repoRoot -InstallPackages:$InstallDeps
    }
}
