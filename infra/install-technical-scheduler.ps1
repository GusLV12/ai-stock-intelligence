# Registra el programador local sin guardar credenciales ni modificar Quant Lab.
$taskRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\apps\backend')).Path
$taskPython = Join-Path $taskRoot '.venv\Scripts\pythonw.exe'
$taskManage = Join-Path $taskRoot 'manage.py'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'No se encontró el entorno de Python del backend.' }
$taskAction = New-ScheduledTaskAction -Execute $taskPython -Argument ('"' + $taskManage + '" refresh_technical_portfolio --scheduled') -WorkingDirectory $taskRoot
$taskRepeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 3650)
$taskLogon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$taskSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName 'AIStock-TechnicalPortfolio' -Action $taskAction -Trigger @($taskRepeat, $taskLogon) -Settings $taskSettings -Description 'Actualización aislada de velas técnicas de cartera después del cierre de Nueva York.' -Force
