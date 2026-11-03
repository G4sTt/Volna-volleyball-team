param(
    [ValidatePattern('^[a-zA-Z0-9-]+$')]
    [string]$Username = 'G4sTt'
)
$ErrorActionPreference = 'Stop'
$kubectlCommand = Get-Command kubectl -ErrorAction Stop
$projectRoot = Split-Path $PSScriptRoot -Parent
$cacheDirectory = Join-Path $projectRoot '.run/kube-cache'
$kubeArguments = @('--context', 'docker-desktop', '--namespace', 'default', '--cache-dir', $cacheDirectory)

# Confirm the cluster is reachable before asking for a token.
& $kubectlCommand.Source @kubeArguments get nodes --request-timeout=10s
if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop and wait for Kubernetes before creating the registry Secret.' }

Write-Host 'Enter a GitHub personal access token (classic) with read:packages only.'
$secureToken = Read-Host 'GHCR read token (hidden)' -AsSecureString
$tokenPointer = [IntPtr]::Zero
try {
    $tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
    $tokenText = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer)
    if ([string]::IsNullOrWhiteSpace($tokenText)) { throw 'The token must not be empty.' }

    $authText = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("${Username}:$tokenText"))
    $dockerConfig = @{ auths = @{ 'ghcr.io' = @{ auth = $authText } } } | ConvertTo-Json -Depth 5 -Compress
    $secret = @{
        apiVersion = 'v1'
        kind = 'Secret'
        metadata = @{ name = 'ghcr-credentials'; namespace = 'default' }
        type = 'kubernetes.io/dockerconfigjson'
        data = @{ '.dockerconfigjson' = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($dockerConfig)) }
    } | ConvertTo-Json -Depth 6 -Compress

    # stdin avoids putting the token into process arguments or a file on disk.
    # Server-side apply avoids storing a second copy in the last-applied annotation.
    $secret | & $kubectlCommand.Source @kubeArguments apply --server-side --field-manager=volna-ghcr -f -
    if ($LASTEXITCODE -ne 0) { throw 'Could not save the registry Secret.' }
    Write-Host 'GHCR read access saved as Secret ghcr-credentials in namespace default.'
} finally {
    if ($tokenPointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer) }
    $tokenText = $null
    $authText = $null
    $dockerConfig = $null
    $secret = $null
    if ($secureToken) { $secureToken.Dispose() }
}
