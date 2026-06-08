param(
  [string]$PythonPath = "",
  [int]$RestartDelaySeconds = 5
)

$ErrorActionPreference = "Stop"
$RepoDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptPath = Join-Path $RepoDir "runner_supervisor.py"
$LogPath = Join-Path $RepoDir "runner-supervisor.log"

function Resolve-PythonPath {
  param([string]$Preferred)

  if ($Preferred -and (Test-Path $Preferred)) {
    return (Resolve-Path $Preferred).Path
  }

  $pythonCmd = Get-Command python.exe -ErrorAction SilentlyContinue
  if ($pythonCmd) {
    return $pythonCmd.Source
  }

  $pyCmd = Get-Command py.exe -ErrorAction SilentlyContinue
  if ($pyCmd) {
    return $pyCmd.Source
  }

  throw "Nao foi possivel localizar python.exe ou py.exe. Informe -PythonPath."
}

$Python = Resolve-PythonPath -Preferred $PythonPath
Set-Location $RepoDir

while ($true) {
  $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  Add-Content $LogPath "$timestamp | START supervisor | python=$Python"

  try {
    & $Python $ScriptPath
    $exitCode = $LASTEXITCODE
  } catch {
    $exitCode = -1
    Add-Content $LogPath "$timestamp | EXCEPTION $($_.Exception.Message)"
  }

  $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  Add-Content $LogPath "$timestamp | STOP exitCode=$exitCode"
  Start-Sleep -Seconds $RestartDelaySeconds
}
