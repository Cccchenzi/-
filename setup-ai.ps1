param([switch]$WithLocalChat)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$RuntimeDirectory = Join-Path $PSScriptRoot '.runtime/ollama'
$OllamaExecutable = Join-Path $RuntimeDirectory 'ollama.exe'
$DownloadPath = Join-Path $PSScriptRoot '.runtime/ollama-v0.40.5.zip'
$ExpectedDigest = '5134ce7e3bc8e94d0494721c799ec293b429450d0c52461b0ee3647dd3bb4bb7'
if (-not (Test-Path -LiteralPath $OllamaExecutable)) {
    New-Item -ItemType Directory -Path (Join-Path $PSScriptRoot '.runtime') -Force | Out-Null
    Write-Host 'Downloading official Ollama 0.40.5 runtime (~1.47 GB)...'
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/ollama/ollama/releases/download/v0.40.5/ollama-windows-amd64.zip' -OutFile $DownloadPath
    if ((Get-FileHash -LiteralPath $DownloadPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedDigest) {
        throw 'Runtime checksum mismatch. No executable was started.'
    }
    Expand-Archive -LiteralPath $DownloadPath -DestinationPath $RuntimeDirectory -Force
    Remove-Item -LiteralPath $DownloadPath
}
$DataDirectory = if ($env:DATA_DIR) { $env:DATA_DIR } else { 'data' }
$EnvironmentFile = Join-Path $PSScriptRoot '.env'
if (-not $env:DATA_DIR -and (Test-Path -LiteralPath $EnvironmentFile)) {
    foreach ($Line in (Get-Content -LiteralPath $EnvironmentFile)) {
        if ($Line -match '^\s*DATA_DIR\s*=\s*(.*?)\s*$') { $DataDirectory = $Matches[1].Trim().Trim('"').Trim("'") }
    }
}
if ($DataDirectory.StartsWith('~')) { $DataDirectory = Join-Path ([Environment]::GetFolderPath('UserProfile')) $DataDirectory.Substring(1).TrimStart('/','\') }
if (-not [IO.Path]::IsPathRooted($DataDirectory)) { $DataDirectory = Join-Path $PSScriptRoot $DataDirectory }
$DataDirectory = [IO.Path]::GetFullPath($DataDirectory)
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_MODELS = Join-Path $DataDirectory 'ollama-models'
$env:OLLAMA_NO_CLOUD = '1'
$RegistryUri = [Uri]'https://registry.ollama.ai'
$SystemProxy = [System.Net.WebRequest]::DefaultWebProxy.GetProxy($RegistryUri)
if ($SystemProxy.Host -ne $RegistryUri.Host -and -not $env:HTTPS_PROXY) { $env:HTTPS_PROXY = $SystemProxy.AbsoluteUri }
$env:NO_PROXY = 'localhost,127.0.0.1,::1'
New-Item -ItemType Directory -Path $DataDirectory -Force | Out-Null
try { $null = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2 }
catch {
    Start-Process -FilePath $OllamaExecutable -ArgumentList 'serve' -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $DataDirectory 'ollama-stdout.log') `
        -RedirectStandardError (Join-Path $DataDirectory 'ollama-stderr.log')
    $ServiceReady = $false
    for ($Attempt = 0; $Attempt -lt 45; $Attempt++) {
        Start-Sleep -Seconds 1
        try { $null = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2; $ServiceReady = $true; break }
        catch { }
    }
    if (-not $ServiceReady) { throw 'Ollama did not start. Check data/ollama-stderr.log.' }
}
Write-Host 'Downloading qwen3-embedding:0.6b (~639 MB) from the official registry...'
$ProgressPreference = 'SilentlyContinue'
$ManifestResponse = Invoke-WebRequest -UseBasicParsing -Uri 'https://registry.ollama.ai/v2/library/qwen3-embedding/manifests/0.6b'
$ManifestText = if ($ManifestResponse.Content -is [byte[]]) { [Text.Encoding]::UTF8.GetString($ManifestResponse.Content) } else { $ManifestResponse.Content }
$Manifest = $ManifestText | ConvertFrom-Json
$BlobDirectory = Join-Path $env:OLLAMA_MODELS 'blobs'
New-Item -ItemType Directory -Path $BlobDirectory -Force | Out-Null
foreach ($Layer in (@($Manifest.config) + @($Manifest.layers))) {
    if ($Layer.digest -notmatch '^sha256:[0-9a-f]{64}$') { throw 'Invalid model digest from registry.' }
    $ExpectedHash = $Layer.digest.Substring(7)
    $BlobPath = Join-Path $BlobDirectory ($Layer.digest.Replace(':','-'))
    if ((Test-Path -LiteralPath $BlobPath) -and (Get-FileHash -LiteralPath $BlobPath -Algorithm SHA256).Hash.ToLowerInvariant() -eq $ExpectedHash) { continue }
    $PartialPath = "$BlobPath.download"
    Invoke-WebRequest -UseBasicParsing -Uri ("https://registry.ollama.ai/v2/library/qwen3-embedding/blobs/" + $Layer.digest) -OutFile $PartialPath
    if ((Get-FileHash -LiteralPath $PartialPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedHash) { throw 'Model checksum mismatch.' }
    Move-Item -LiteralPath $PartialPath -Destination $BlobPath -Force
}
$ManifestDirectory = Join-Path $env:OLLAMA_MODELS 'manifests/registry.ollama.ai/library/qwen3-embedding'
New-Item -ItemType Directory -Path $ManifestDirectory -Force | Out-Null
[IO.File]::WriteAllText((Join-Path $ManifestDirectory '0.6b'), $ManifestText.Trim(), [Text.UTF8Encoding]::new($false))
try {
    $Probe = @{ model = 'qwen3-embedding:0.6b'; input = @('Local embedding readiness check.') } | ConvertTo-Json -Compress
    $EmbeddingResult = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:11434/api/embed' -ContentType 'application/json' -Body $Probe -TimeoutSec 180
    if (-not $EmbeddingResult.embeddings -or $EmbeddingResult.embeddings[0].Count -lt 1) { throw 'No vector returned.' }
} catch {
    throw "The running Ollama cannot use the installed embedding model. Another Ollama instance may use a different model directory. Stop that instance yourself, then rerun setup-ai.bat. Expected OLLAMA_MODELS: $env:OLLAMA_MODELS. Installation has not passed readiness validation."
}
if ($WithLocalChat) {
    & $OllamaExecutable pull 'qwen3:4b'
    if ($LASTEXITCODE -ne 0) { throw 'Chat model download failed.' }
}
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.env'))) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot '.env.example') -Destination (Join-Path $PSScriptRoot '.env')
}
Write-Host 'Local embeddings are ready. Set DEEPSEEK_API_KEY in .env, restart Zhiwei, then check the model connection.' -ForegroundColor Green
Write-Host 'For fully local chat, set RAG_PROVIDER=ollama in .env and restart.'
