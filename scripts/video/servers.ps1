# Start / stop the two replay servers (act 2 on :8080, act 1 on :8081). Tracks its own PIDs; never touches other processes.
param([ValidateSet('start','stop','status')][string]$Action = 'start')
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Work = Join-Path $Root 'deliverables\video\work'
New-Item -ItemType Directory -Force $Work | Out-Null
$PidFile = Join-Path $Work 'servers.pid'
$Py = Join-Path $Root '.venv\Scripts\python.exe'
$Servers = @(@{act = 2; port = 8080}, @{act = 1; port = 8081})

function Stop-Mine {
    if (Test-Path $PidFile) {
        foreach ($line in Get-Content $PidFile) {
            $id = [int]($line -split ',')[0]
            $p = Get-Process -Id $id -ErrorAction SilentlyContinue
            if ($p -and $p.ProcessName -like 'python*') { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
        }
        Remove-Item $PidFile -Force
    }
}

switch ($Action) {
    'stop' { Stop-Mine; Write-Host 'servers stopped' }
    'status' { if (Test-Path $PidFile) { Get-Content $PidFile } else { Write-Host 'no servers recorded' } }
    'start' {
        Stop-Mine
        $lines = @()
        foreach ($s in $Servers) {
            $log = Join-Path $Work ("serve_act{0}.log" -f $s.act)
            $p = Start-Process -FilePath $Py -ArgumentList @('scripts\video\serve_demo.py', '--act', $s.act, '--port', $s.port) `
                -WorkingDirectory $Root -RedirectStandardOutput $log -RedirectStandardError ($log + '.err') -PassThru -WindowStyle Hidden
            $lines += ("{0},{1}" -f $p.Id, $s.port)
        }
        Set-Content -Path $PidFile -Value $lines
        foreach ($s in $Servers) {
            $ok = $false
            for ($i = 0; $i -lt 90 -and -not $ok; $i++) {
                Start-Sleep -Milliseconds 500
                try { $r = Invoke-WebRequest -UseBasicParsing -Uri ("http://127.0.0.1:{0}/api/health" -f $s.port) -TimeoutSec 2; $ok = ($r.StatusCode -eq 200) } catch { }
            }
            if (-not $ok) { throw ("server for act {0} did not come up; see {1}" -f $s.act, (Join-Path $Work ("serve_act{0}.log.err" -f $s.act))) }
        }
        Write-Host 'servers up: act 2 -> :8080, act 1 -> :8081'
    }
}
