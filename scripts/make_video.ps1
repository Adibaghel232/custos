<#
.SYNOPSIS
  Re-makes the Custos demo video end to end: deliverables\video\custos_demo.mp4 (+ .srt, 720p copy) and deliverables\demo_screenshot.png.

.DESCRIPTION
  1. tools      : ffmpeg (tools\ffmpeg, downloaded once), Playwright (tools\pw, npm)
  2. data       : demo\live\traces_live\trace_act{1,2}.json  ->  console Trace schema + a real CustosGateway (scripts\video\rehydrate.py)
  3. servers    : two replay servers (act 2 on :8080, act 1 on :8081), FastAPI create_app over the rehydrated gateway
  4. voice      : deliverables\voiceover_script.md -> Windows SAPI voice per sentence (or your own WAVs with -OwnVoice)
  5. record     : Playwright + DevTools screencast, shot by shot (1920x1000 UI + 80 px caption band)
  6. assemble   : ffmpeg -> H.264 1080p30 + AAC, burned-in captions

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\make_video.ps1                 # everything
  powershell -ExecutionPolicy Bypass -File scripts\make_video.ps1 -Only S11       # re-shoot the Bench shot only, then re-assemble
  powershell -ExecutionPolicy Bypass -File scripts\make_video.ps1 -SkipRecord     # voice / caption changes only: re-assemble the last recording
  powershell -ExecutionPolicy Bypass -File scripts\make_video.ps1 -OwnVoice       # use deliverables\video\own_voice\S0.wav ... S12.wav
#>
param(
    [string]$Only = '',                 # comma list of shots to re-record, e.g. S11 or S7,S8 (shots that share state are re-recorded together)
    [switch]$SkipRecord,
    [switch]$SkipBuild,                 # do not rebuild ui\dist
    [switch]$OwnVoice,
    [string]$Voice = 'Microsoft Zira Desktop',
    [int]$Rate = 1,
    [switch]$NoEmbed                    # skip the 720p copy
)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
$Py = Join-Path $Root '.venv\Scripts\python.exe'
$Work = Join-Path $Root 'deliverables\video\work'
$Tools = Join-Path $Root 'tools'
New-Item -ItemType Directory -Force $Work, $Tools | Out-Null

function Step($t) { Write-Host ""; Write-Host "== $t" -ForegroundColor Cyan }
function Run($exe, [string[]]$a) { & $exe @a; if ($LASTEXITCODE -ne 0) { throw "failed: $exe $($a -join ' ') (exit $LASTEXITCODE)" } }

if (-not (Test-Path $Py)) { throw "Python venv missing: $Py" }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'node is required' }

# ---------------------------------------------------------------------------------------------- 1. tools
Step 'tools'
$ffmpegDir = Join-Path $Tools 'ffmpeg'
$ffmpeg = Get-ChildItem $ffmpegDir -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $ffmpeg) {
    Write-Host 'downloading a static ffmpeg build into tools\ffmpeg ...'
    $zip = Join-Path $Tools 'ffmpeg-release-essentials.zip'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile $zip
    $tmp = Join-Path $Tools 'ffmpeg_extract'
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    $inner = Get-ChildItem $tmp -Directory | Select-Object -First 1
    Remove-Item $ffmpegDir -Recurse -Force -ErrorAction SilentlyContinue
    Move-Item $inner.FullName $ffmpegDir
    Remove-Item $tmp, $zip -Recurse -Force
    $ffmpeg = Get-ChildItem $ffmpegDir -Recurse -Filter ffmpeg.exe | Select-Object -First 1
}
Write-Host "ffmpeg: $($ffmpeg.FullName)"
$pwDir = Join-Path $Tools 'pw'
if (-not (Test-Path (Join-Path $pwDir 'node_modules\playwright'))) {
    New-Item -ItemType Directory -Force $pwDir | Out-Null
    Push-Location $pwDir
    if (-not (Test-Path 'package.json')) { Run npm @('init', '-y') | Out-Null }
    Run npm @('install', 'playwright@1.49.1')
    Pop-Location
}
$hasEdge = Test-Path "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
if (-not $hasEdge) { Write-Host 'Edge not found: installing Playwright chromium'; Push-Location $pwDir; Run npx @('playwright', 'install', 'chromium'); Pop-Location }

# ---------------------------------------------------------------------------------------------- 2. UI + data
Step 'console build'
if (-not $SkipBuild -and -not $SkipRecord) {
    Push-Location (Join-Path $Root 'ui')
    if (-not (Test-Path 'node_modules')) { Run npm @('install') }
    Run npm @('run', 'build')
    Pop-Location
}
Step 'adapt the recorded live traces (schema check)'
Run $Py @('scripts\video\adapt_trace.py')

# ---------------------------------------------------------------------------------------------- 3. voice
Step 'voiceover script -> shots'
Run $Py @('scripts\video\parse_script.py')
if (-not $OwnVoice) {
    Step "text-to-speech ($Voice, rate $Rate)"
    & "$PSScriptRoot\video\tts.ps1" -Voice $Voice -Rate $Rate
}
$man = @('scripts\video\audio_manifest.py'); if ($OwnVoice) { $man += '--own-voice' }
Run $Py $man

# ---------------------------------------------------------------------------------------------- 4. record
$servers = Join-Path $PSScriptRoot 'video\servers.ps1'
try {
    if (-not $SkipRecord) {
        Step 'replay servers'
        & $servers -Action start
        Step 'record'
        $rec = @('scripts\video\record.mjs'); if ($Only) { $rec += @('--only', $Only) }
        Run node $rec
    }
} finally {
    & $servers -Action stop
}

# ---------------------------------------------------------------------------------------------- 5. assemble
Step 'assemble'
$asm = @('scripts\video\assemble.py'); if ($OwnVoice) { $asm += '--own-voice' }; if ($NoEmbed) { $asm += '--no-embed' }
$env:PATH = "$($ffmpeg.DirectoryName);$env:PATH"
Run $Py $asm

Write-Host ''
Write-Host 'done:' -ForegroundColor Green
Get-ChildItem (Join-Path $Root 'deliverables\video') -File | ForEach-Object { '  {0}  {1:N1} MB' -f $_.FullName, ($_.Length / 1MB) }
if (Test-Path (Join-Path $Root 'deliverables\demo_screenshot.png')) { '  ' + (Join-Path $Root 'deliverables\demo_screenshot.png') }
