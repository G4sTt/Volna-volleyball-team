param(
    [switch]$SkipBuild,
    [int]$SitePort = 8088,
    [int]$PgAdminPort = 8085,
    [switch]$ApiPorts
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$runDirectory = Join-Path $projectRoot '.run'
New-Item -ItemType Directory -Path $runDirectory -Force | Out-Null
$kubectlCommand = Get-Command kubectl -ErrorAction Stop
$contextName = 'docker-desktop'

function Invoke-Kube {
    & $kubectlCommand.Source --context $contextName --cache-dir (Join-Path $runDirectory 'kube-cache') @args
    if ($LASTEXITCODE -ne 0) { throw "kubectl failed: $args" }
}

Invoke-Kube get nodes --request-timeout=10s
if (-not $SkipBuild) {
    $pythonCommand = Join-Path $projectRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $pythonCommand)) { $pythonCommand = (Get-Command python -ErrorAction Stop).Source }
    & $pythonCommand (Join-Path $PSScriptRoot 'build-in-cluster.py')
    if ($LASTEXITCODE -ne 0) { throw 'Image build failed. Check the build Pod logs.' }
}
$buildFile = Join-Path $runDirectory 'build.json'
if (-not (Test-Path -LiteralPath $buildFile)) { throw 'No previous build. Run this script without -SkipBuild.' }
$buildInfo = Get-Content -LiteralPath $buildFile -Raw | ConvertFrom-Json
$pythonCommand = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonCommand)) { $pythonCommand = (Get-Command python -ErrorAction Stop).Source }
& $pythonCommand (Join-Path $PSScriptRoot 'import-images.py')
if ($LASTEXITCODE -ne 0) { throw 'Image import failed. Check the import Pod logs in volleyball-build.' }
$renderedDirectory = Join-Path $runDirectory 'manifests'
New-Item -ItemType Directory -Path $renderedDirectory -Force | Out-Null
foreach ($component in @('accounts', 'team-info', 'frontend', 'pgadmin')) {
    $sourceText = [System.IO.File]::ReadAllText((Join-Path $projectRoot "K8s/$component.yaml"))
    $renderedText = $sourceText.Replace(':k8s-v1', ':' + $buildInfo.tag)
    [System.IO.File]::WriteAllText((Join-Path $renderedDirectory "$component.yaml"), $renderedText, [System.Text.UTF8Encoding]::new($false))
}
Invoke-Kube apply -f $renderedDirectory
foreach ($databaseName in @('accounts-db', 'team-info-db')) {
    Invoke-Kube rollout status "statefulset/$databaseName" --timeout=240s
}
foreach ($deploymentName in @('accounts', 'team-info', 'frontend', 'pgadmin')) {
    Invoke-Kube rollout status "deployment/$deploymentName" --timeout=300s
}

& (Join-Path $PSScriptRoot 'stop-k8s.ps1')
$forwarders = @()
function Start-Forwarder([string]$serviceName, [int]$localPort, [int]$remotePort) {
    $portProbe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $localPort)
    try { $portProbe.Start() }
    catch { throw "Cannot bind local port $localPort. Close the previous start terminal with Ctrl+C or choose another local port." }
    finally { $portProbe.Stop() }
    $arguments = @('--context', $contextName, 'port-forward', "service/$serviceName", "${localPort}:$remotePort", '--address=127.0.0.1')
    $processInfo = Start-Process -FilePath $kubectlCommand.Source -ArgumentList $arguments -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runDirectory "$serviceName-forward.log") `
        -RedirectStandardError (Join-Path $runDirectory "$serviceName-forward.err.log")
    Start-Sleep -Milliseconds 500
    if ($processInfo.HasExited) { throw "Port forwarding failed for $serviceName; see .run/$serviceName-forward.err.log" }
    return [pscustomobject]@{ pid = $processInfo.Id; started = $processInfo.StartTime.ToUniversalTime().ToString('O'); service = $serviceName; port = $localPort }
}
try {
    $forwarders += Start-Forwarder 'frontend' $SitePort 80
    $forwarders += Start-Forwarder 'pgadmin' $PgAdminPort 80
    if ($ApiPorts) {
        $forwarders += Start-Forwarder 'accounts' 8002 8000
        $forwarders += Start-Forwarder 'team-info' 8003 8000
    }
} catch {
    foreach ($entry in $forwarders) {
        Stop-Process -Id $entry.pid -ErrorAction SilentlyContinue
    }
    throw
}
ConvertTo-Json -InputObject @($forwarders) | Set-Content -LiteralPath (Join-Path $runDirectory 'forwarders.json') -Encoding utf8
Write-Host "Site: http://localhost:$SitePort"
Write-Host "pgAdmin: http://localhost:$PgAdminPort"
Write-Host 'pgAdmin login: admin@example.com; password: pgadmin_lab_password'
Write-Host 'API documentation, when -ApiPorts is enabled: localhost:8002/docs and localhost:8003/docs'
Write-Host 'Stop local port forwarding: ./scripts/stop-k8s.ps1 (cluster resources and data remain).'
Write-Host 'Keep this terminal open. Press Ctrl+C to stop local port forwarding.'
try {
    while ($true) {
        Start-Sleep -Seconds 2
        foreach ($entry in $forwarders) {
            if (-not (Get-Process -Id $entry.pid -ErrorAction SilentlyContinue)) {
                throw "Port forwarding stopped for $($entry.service). See its log in .run."
            }
        }
    }
} finally {
    & (Join-Path $PSScriptRoot 'stop-k8s.ps1') -OnlyPids @($forwarders | ForEach-Object { $_.pid })
}
