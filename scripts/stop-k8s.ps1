param([int[]]$OnlyPids)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$stateFile = Join-Path $projectRoot '.run/forwarders.json'
if (Test-Path -LiteralPath $stateFile) {
    # Windows PowerShell 5.1 returns the JSON array as one pipeline object.
    # Assign it directly so foreach visits individual process records in both 5.1 and 7.
    $entries = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
    $kept = @()
    foreach ($entry in $entries) {
        if ($null -eq $entry) { continue }
        if ($OnlyPids -and $entry.pid -notin $OnlyPids) { $kept += $entry; continue }
        $processInfo = Get-Process -Id $entry.pid -ErrorAction SilentlyContinue
        if ($processInfo -and $processInfo.ProcessName -eq 'kubectl' -and `
            $processInfo.StartTime.ToUniversalTime().Ticks -eq ([datetime]$entry.started).ToUniversalTime().Ticks) {
            Stop-Process -Id $processInfo.Id
            Wait-Process -Id $processInfo.Id -Timeout 10 -ErrorAction SilentlyContinue
        }
    }
    if ($kept.Count) {
        ConvertTo-Json -InputObject @($kept) | Set-Content -LiteralPath $stateFile -Encoding utf8
    } else { Remove-Item -LiteralPath $stateFile }
}
