param(
    [switch]$SkipBuild,
    [int]$SitePort = 8088,
    [int]$PgAdminPort = 8085,
    [switch]$ApiPorts,
    [switch]$UseGhcr,
    [ValidatePattern('^(sha-[a-f0-9]{40})?$')]
    [string]$ImageTag = '',
    [ValidatePattern('^[a-zA-Z0-9-]+$')]
    [string]$RegistryOwner = 'g4stt',
    [ValidatePattern('^([a-z0-9]([-a-z0-9.]*[a-z0-9])?)?$')]
    [string]$ImagePullSecret = 'ghcr-credentials',
    [switch]$RenderOnly,
    [switch]$PortForwardOnly
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$runDirectory = Join-Path $projectRoot '.run'
New-Item -ItemType Directory -Path $runDirectory -Force | Out-Null
$kubectlCommand = Get-Command kubectl -ErrorAction Stop
$contextName = 'docker-desktop'
$namespaceName = 'default'

if ($UseGhcr -and -not $ImageTag) {
    throw 'Use -ImageTag sha-<40-character-commit> from a successful publishing workflow.'
}
if ($UseGhcr -and $SkipBuild) {
    throw '-UseGhcr already skips local builds and imports; omit -SkipBuild.'
}
if (-not $UseGhcr -and $ImageTag) {
    throw '-ImageTag is used with -UseGhcr only.'
}
if ($PortForwardOnly -and ($UseGhcr -or $SkipBuild -or $ImageTag -or $RenderOnly)) {
    throw 'Use -PortForwardOnly by itself, with optional port parameters and -ApiPorts.'
}

function Invoke-Kube {
    & $kubectlCommand.Source --context $contextName --namespace $namespaceName --cache-dir (Join-Path $runDirectory 'kube-cache') @args
    if ($LASTEXITCODE -ne 0) { throw "kubectl failed: $args" }
}

if (-not $PortForwardOnly) {
if (-not $RenderOnly) {
    Invoke-Kube get nodes --request-timeout=10s
    $argoCrd = & $kubectlCommand.Source --context $contextName get crd applications.argoproj.io -o name --ignore-not-found --request-timeout=10s
    if ($LASTEXITCODE -ne 0) { throw 'Could not check whether Argo CD manages this application.' }
    if ($argoCrd) {
        $argoApplication = & $kubectlCommand.Source --context $contextName -n argocd get application volna -o name --ignore-not-found --request-timeout=10s
        if ($LASTEXITCODE -ne 0) { throw 'Could not check the Argo CD Application.' }
        if ($argoApplication) { throw 'Argo CD manages VOLNA. Publish changes through CI; use -PortForwardOnly to open the site.' }
    }
    if ($UseGhcr -and $ImagePullSecret) {
        & $kubectlCommand.Source --context $contextName --namespace $namespaceName get secret $ImagePullSecret -o name --request-timeout=10s
        if ($LASTEXITCODE -ne 0) {
            throw 'Registry Secret is missing. Run scripts/set-ghcr-secret.ps1 first. For public images use -ImagePullSecret "".'
        }
    }
}
if (-not $UseGhcr -and -not $SkipBuild -and -not $RenderOnly) {
    $pythonCommand = Join-Path $projectRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $pythonCommand)) { $pythonCommand = (Get-Command python -ErrorAction Stop).Source }
    & $pythonCommand (Join-Path $PSScriptRoot 'build-in-cluster.py')
    if ($LASTEXITCODE -ne 0) { throw 'Image build failed. Check the build Pod logs.' }
}
if (-not $UseGhcr) {
    $buildFile = Join-Path $runDirectory 'build.json'
    if (-not (Test-Path -LiteralPath $buildFile)) { throw 'No previous build. Run this script without -SkipBuild.' }
    $buildInfo = Get-Content -LiteralPath $buildFile -Raw | ConvertFrom-Json
    if (-not $RenderOnly) {
        $pythonCommand = Join-Path $projectRoot '.venv/Scripts/python.exe'
        if (-not (Test-Path -LiteralPath $pythonCommand)) { $pythonCommand = (Get-Command python -ErrorAction Stop).Source }
        & $pythonCommand (Join-Path $PSScriptRoot 'import-images.py')
        if ($LASTEXITCODE -ne 0) { throw 'Image import failed. Check the import Pod logs in volleyball-build.' }
    }
}
$manifestFolder = if ($UseGhcr) { 'manifests-ghcr' } else { 'manifests' }
$renderedDirectory = Join-Path $runDirectory $manifestFolder
New-Item -ItemType Directory -Path $renderedDirectory -Force | Out-Null
foreach ($component in @('accounts', 'team-info', 'frontend', 'pgadmin')) {
    $sourceText = [System.IO.File]::ReadAllText((Join-Path $projectRoot "K8s/$component.yaml"))
    $renderedText = $sourceText
    if ($UseGhcr -and $component -ne 'pgadmin') {
        $sourceImage = "localhost:32000/volleyball-team-${component}:k8s-v1"
        if (-not $sourceText.Contains($sourceImage)) { throw "Expected image placeholder is missing in K8s/$component.yaml" }
        $targetImage = "ghcr.io/$($RegistryOwner.ToLowerInvariant())/volleyball-team-${component}:$ImageTag"
        $renderedText = $renderedText.Replace($sourceImage, $targetImage)
        if ($ImagePullSecret) {
            $initMarker = '      initContainers:'
            if (-not $renderedText.Contains($initMarker)) { throw "Pod template marker is missing in K8s/$component.yaml" }
            $renderedText = $renderedText.Replace($initMarker, "      imagePullSecrets:`n      - name: $ImagePullSecret`n$initMarker")
        }
    } elseif (-not $UseGhcr) {
        $renderedText = $renderedText.Replace(':k8s-v1', ':' + $buildInfo.tag)
    }
    [System.IO.File]::WriteAllText((Join-Path $renderedDirectory "$component.yaml"), $renderedText, [System.Text.UTF8Encoding]::new($false))
}
if ($RenderOnly) {
    Write-Host "Rendered manifests: $renderedDirectory (no cluster changes)."
    return
}
if ($UseGhcr) { Write-Host "Deploying GHCR images: $ImageTag" }
Invoke-Kube apply -f $renderedDirectory
foreach ($databaseName in @('accounts-db', 'team-info-db')) {
    Invoke-Kube rollout status "statefulset/$databaseName" --timeout=240s
}
foreach ($deploymentName in @('accounts', 'team-info', 'frontend', 'pgadmin')) {
    Invoke-Kube rollout status "deployment/$deploymentName" --timeout=300s
}
} else {
    Invoke-Kube get nodes --request-timeout=10s
    $forwardedServices = @('frontend', 'pgadmin')
    if ($ApiPorts) { $forwardedServices += @('accounts', 'team-info') }
    $missingServices = @()
    foreach ($serviceName in $forwardedServices) {
        $existingService = & $kubectlCommand.Source --context $contextName --namespace $namespaceName get service $serviceName -o name --ignore-not-found --request-timeout=10s
        if ($LASTEXITCODE -ne 0) { throw "Could not check service/$serviceName in namespace $namespaceName." }
        if (-not $existingService) { $missingServices += $serviceName }
    }
    if ($missingServices.Count) {
        throw "VOLNA is not deployed in context $contextName, namespace $namespaceName. Missing services: $($missingServices -join ', '). -PortForwardOnly opens an existing deployment; a Ready node alone is not enough. For a new or reset cluster, run scripts/set-ghcr-secret.ps1, then scripts/install-argocd.ps1 and wait for Application volna to become Synced/Healthy before retrying. See README for cluster recovery."
    }
    foreach ($serviceName in $forwardedServices) {
        Invoke-Kube rollout status "deployment/$serviceName" --timeout=300s
    }
}

& (Join-Path $PSScriptRoot 'stop-k8s.ps1')
$forwarders = @()
function Start-Forwarder([string]$serviceName, [int]$localPort, [int]$remotePort) {
    $portProbe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $localPort)
    try { $portProbe.Start() }
    catch { throw "Cannot bind local port $localPort. Close the previous start terminal with Ctrl+C or choose another local port." }
    finally { $portProbe.Stop() }
    $arguments = @('--context', $contextName, '--namespace', $namespaceName, 'port-forward', "service/$serviceName", "${localPort}:$remotePort", '--address=127.0.0.1')
    $processInfo = Start-Process -FilePath $kubectlCommand.Source -ArgumentList $arguments -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runDirectory "$serviceName-forward.log") `
        -RedirectStandardError (Join-Path $runDirectory "$serviceName-forward.err.log")
    Start-Sleep -Milliseconds 500
    if ($processInfo.HasExited) { throw "Port forwarding failed for $serviceName; see .run/$serviceName-forward.err.log" }
    return [pscustomobject]@{ pid = $processInfo.Id; started = $processInfo.StartTime.ToUniversalTime().ToString('O'); service = $serviceName; port = $localPort; remotePort = $remotePort }
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
        for ($forwarderIndex = 0; $forwarderIndex -lt $forwarders.Count; $forwarderIndex++) {
            $entry = $forwarders[$forwarderIndex]
            if (-not (Get-Process -Id $entry.pid -ErrorAction SilentlyContinue)) {
                try {
                    $forwarders[$forwarderIndex] = Start-Forwarder $entry.service $entry.port $entry.remotePort
                    ConvertTo-Json -InputObject @($forwarders) | Set-Content -LiteralPath (Join-Path $runDirectory 'forwarders.json') -Encoding utf8
                    Write-Host "Reconnected local port forwarding for $($entry.service)."
                } catch {
                    Write-Host "Waiting to reconnect $($entry.service) after a Pod change. See .run/$($entry.service)-forward.err.log."
                }
            }
        }
    }
} finally {
    & (Join-Path $PSScriptRoot 'stop-k8s.ps1') -OnlyPids @($forwarders | ForEach-Object { $_.pid })
}
