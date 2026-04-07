# Instala Task Scheduler do Windows para rodar geo_finops.sync diariamente as 23:50.
#
# Uso (PowerShell como admin nao necessario):
#   powershell -ExecutionPolicy Bypass -File install_scheduler.ps1
#
# Para desinstalar:
#   schtasks /Delete /TN "GeoFinOpsSync" /F

$TaskName = "GeoFinOpsSync"
$PythonExe = (Get-Command python).Source
$RepoPath = "C:\Sandyboxclaude\geo-finops"
$LogDir = "$env:USERPROFILE\.config\geo-finops\logs"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

$LogFile = "$LogDir\sync.log"
$Action = New-ScheduledTaskAction -Execute $PythonExe `
    -Argument "-m geo_finops.sync" `
    -WorkingDirectory $RepoPath

$Trigger = New-ScheduledTaskTrigger -Daily -At "23:50"

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -LogonType S4U -RunLevel Limited

# Remove se ja existe
schtasks /Query /TN $TaskName 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Removendo task existente..."
    schtasks /Delete /TN $TaskName /F | Out-Null
}

Register-ScheduledTask -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "GeoFinOps daily sync to Supabase (geo-finops)" | Out-Null

Write-Host "Task '$TaskName' instalada. Roda diariamente as 23:50."
Write-Host "Logs em: $LogFile"
Write-Host ""
Write-Host "Para testar manualmente agora:"
Write-Host "  schtasks /Run /TN $TaskName"
Write-Host ""
Write-Host "Para desinstalar:"
Write-Host "  schtasks /Delete /TN $TaskName /F"
