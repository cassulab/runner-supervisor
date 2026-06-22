param(
  [string]$RepoDir = "C:\repos\scriptreceitabx",
  [string]$JobsDir = "C:\RunnerPAD\jobs",
  [string]$CurrentRunFile = "C:\RunnerPAD\current_run.txt",
  [int]$Port = 5050
)

$ErrorActionPreference = "SilentlyContinue"

Write-Host "[ReceitaBX] Parando processos..."

$targets = @()
Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object {
    if ($_ -and $_ -gt 0) { $targets += [int]$_ }
  }

Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object {
    $_.CommandLine -and (
      $_.CommandLine -like "*scriptreceitabx*" -or
      $_.CommandLine -like "*run-receitabx-runner.ps1*" -or
      $_.CommandLine -like "*agenda_pad.py*"
    )
  } |
  ForEach-Object { $targets += [int]$_.ProcessId }

$all = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
$changed = $true
while ($changed) {
  $changed = $false
  foreach ($process in $all) {
    if ($process.ParentProcessId -in $targets -and $process.ProcessId -notin $targets) {
      $targets += [int]$process.ProcessId
      $changed = $true
    }
  }
}

foreach ($processId in ($targets | Select-Object -Unique | Sort-Object -Descending)) {
  Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 2

Write-Host "[ReceitaBX] Limpando fila e travas..."

$removed = 0
if (Test-Path -LiteralPath $CurrentRunFile) {
  Remove-Item -LiteralPath $CurrentRunFile -Force -ErrorAction SilentlyContinue
  $removed++
}

if (Test-Path -LiteralPath $JobsDir) {
  Get-ChildItem -LiteralPath $JobsDir -File -Force -ErrorAction SilentlyContinue |
    Where-Object {
      $_.Name -eq "runner_busy.lock" -or
      $_.Name -eq "pending_downloads.json" -or
      $_.Extension -eq ".json"
    } |
    ForEach-Object {
      Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
      $removed++
    }
}

Write-Host "[ReceitaBX] Itens removidos: $removed"

$startScript = Join-Path $RepoDir "run-receitabx-runner.ps1"
if (Test-Path -LiteralPath $startScript) {
  Write-Host "[ReceitaBX] Iniciando runner..."
  Start-Process powershell.exe `
    -WindowStyle Hidden `
    -WorkingDirectory $RepoDir `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $startScript)
} else {
  Write-Host "[ReceitaBX] Script nao encontrado: $startScript"
}

Start-Sleep -Seconds 4

try {
  Invoke-RestMethod "http://localhost:$Port/status" -TimeoutSec 3 | ConvertTo-Json -Depth 5
} catch {
  Write-Host "[ReceitaBX] Runner ainda nao respondeu na porta $Port."
}
