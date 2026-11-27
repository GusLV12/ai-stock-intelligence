param()

$ErrorActionPreference = 'Stop'
$raiz = $PSScriptRoot
$backend = Join-Path $raiz 'apps\backend'
$web = Join-Path $raiz 'apps\web'
$python = Join-Path $backend '.venv\Scripts\python.exe'
$next = Join-Path $web 'node_modules\next\dist\bin\next'
$node = (Get-Command node.exe -ErrorAction Stop).Source
$npm = (Get-Command npm.cmd -ErrorAction Stop).Source

if (-not (Test-Path $python) -or -not (Test-Path $next)) {
    throw 'Faltan dependencias. Sigue la instalacion inicial del README.'
}

foreach ($puerto in 8010, 3010) {
    $cliente = [System.Net.Sockets.TcpClient]::new()
    try {
        $conexion = $cliente.BeginConnect('127.0.0.1', $puerto, $null, $null)
        if ($conexion.AsyncWaitHandle.WaitOne(500) -and $cliente.Connected) {
            throw "El puerto $puerto ya esta ocupado. Deten el servicio que lo usa antes de continuar."
        }
    } finally {
        $cliente.Close()
    }
}

$env:DJANGO_CSRF_TRUSTED_ORIGINS = 'http://localhost:3010'
$env:BACKEND_URL = 'http://127.0.0.1:8010'
$registros = Join-Path ([System.IO.Path]::GetTempPath()) ("ai-stock-intelligence-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $registros | Out-Null

Write-Host 'Compilando la pantalla web...'
Push-Location $web
try {
    & $npm run build
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo compilar la pantalla web.' }
} finally { Pop-Location }

function Esperar-Servicio($url, $proceso, $nombre) {
    for ($intento = 0; $intento -lt 60; $intento++) {
        if ($proceso.HasExited) { throw "$nombre termino antes de estar disponible. Revisa los registros en $registros" }
        try {
            $respuesta = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
            if ($respuesta.StatusCode -eq 200) { return }
        } catch { }
        Start-Sleep -Seconds 1
    }
    throw "$nombre no esta listo. Revisa los registros en $registros"
}

Write-Host 'Iniciando backend...'
$api = Start-Process -FilePath $python -ArgumentList 'manage.py', 'runserver', '127.0.0.1:8010', '--noreload' -WorkingDirectory $backend -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $registros 'backend.log') -RedirectStandardError (Join-Path $registros 'backend-error.log')
try {
    Esperar-Servicio 'http://127.0.0.1:8010/api/v1/ready' $api 'El backend'
    Write-Host 'Iniciando pantalla web...'
    $pantalla = Start-Process -FilePath $node -ArgumentList ('"' + $next + '"'), 'start', '-p', '3010' -WorkingDirectory $web -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $registros 'web.log') -RedirectStandardError (Join-Path $registros 'web-error.log')
    Esperar-Servicio 'http://localhost:3010/api/v1/ready' $pantalla 'La pantalla web'
} catch {
    if ($pantalla -and -not $pantalla.HasExited) { Stop-Process -Id $pantalla.Id }
    if (-not $api.HasExited) { Stop-Process -Id $api.Id }
    throw
}

Write-Host 'Listo: http://localhost:3010'
Write-Host "Registros: $registros"
Write-Host "Para detenerlos: Stop-Process -Id $($api.Id),$($pantalla.Id)"
