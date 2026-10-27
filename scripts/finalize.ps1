<#
.SYNOPSIS
  Final assembly on the laptop once the GPU queue has written runs/metrics.json:
  pull results -> results/final, console Bench data, S11 narration, deck, public-benchmarks card, re-shoot S11, re-assemble.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\finalize.ps1
  powershell -ExecutionPolicy Bypass -File scripts\finalize.ps1 -NoVideo
#>
param([switch]$NoVideo, [switch]$NoPull, [string]$DemoNote = '')
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
$Py = Join-Path $Root '.venv\Scripts\python.exe'
$env:PYTHONIOENCODING = 'utf-8'
$Final = Join-Path $Root 'results\final'
function Step($t) { Write-Host ""; Write-Host "== $t" -ForegroundColor Cyan }

if (-not $NoPull) {
    Step 'pull results from the GPU box (via the lab PC)'
    New-Item -ItemType Directory -Force $Final | Out-Null
    $files = 'metrics.json final.json FINAL.md cells.csv compute.csv scores.csv run.json run_meta.json latency_raw.csv adaptive.csv utility.csv provenance.csv configs.csv figures external_poisonedrag/summary.json external_agentdojo/summary.json'
    $tar = Join-Path $env:TEMP 'custos_final.tgz'
    & ssh 10.81.30.68 "ssh custos-gpu 'cd ~/custos/runs && tar czf - --ignore-failed-read $files 2>/dev/null'" > $tar
    & tar -xzf $tar -C $Final
    $sec = (& ssh 10.81.30.68 "ssh custos-gpu 'cut -d. -f1 /proc/uptime'" | Select-Object -Last 1).Trim()
    $up = [math]::Round([double]$sec / 3600, 0)
    Write-Host "GPU box uptime hours: $up (8 x A100)"
    $env:CUSTOS_GPU_HOURS = [string]($up * 8)
    $usd = ''; if ($env:CUSTOS_GPU_USD_PER_HOUR) { $usd = [string]([math]::Round($up * [double]$env:CUSTOS_GPU_USD_PER_HOUR, 0)) }
    "instance,gpu,n_gpus,hours,usd`ncustos-gpu,A100-SXM4-80GB,8,$up,$usd" | Set-Content -Encoding utf8 (Join-Path $Final 'compute.csv')
    Get-ChildItem $Final | Format-Table Name, Length
}
if (-not (Test-Path (Join-Path $Final 'metrics.json'))) { throw "results\final\metrics.json missing" }
$ext = $Final   # external_* summaries live next to metrics.json

Step 'console Bench data'
$gh = @(); if ($env:CUSTOS_GPU_HOURS) { $gh = @('--gpu-hours', $env:CUSTOS_GPU_HOURS) }
& $Py scripts\export_ui_bench.py $Final --run-id ("final-" + (Get-Date -Format 'yyyyMMdd')) @gh
if ($LASTEXITCODE) { throw 'export_ui_bench failed' }

Step 'S11 narration from the result files'
& $Py scripts\video\s11_text.py --final (Join-Path $Final 'final.json') --ext $ext
if ($LASTEXITCODE) { throw 's11_text failed' }

Step 'deck'
$deck = Join-Path $Root 'deliverables\Custos_AEGIS2026_Final.pptx'
$dargs = @('scripts\build_deck.py', '--results', $Final, '--out', $deck)
if ($DemoNote) { $dargs += @('--demo-note', $DemoNote) }
& $Py @dargs
if ($LASTEXITCODE) { throw 'build_deck failed' }

Step 'deck slides -> PNG (public-benchmarks card + QA images)'
$qa = Join-Path $Root 'deliverables\deck_png'
New-Item -ItemType Directory -Force $qa | Out-Null
$pp = New-Object -ComObject PowerPoint.Application
$p = $pp.Presentations.Open($deck, $true, $false, $false)
for ($i = 1; $i -le $p.Slides.Count; $i++) { $p.Slides($i).Export((Join-Path $qa ("slide{0:D2}.png" -f $i)), 'PNG', 1920, 1080) }
$pub = $null
for ($i = 1; $i -le $p.Slides.Count; $i++) {
    foreach ($sh in $p.Slides($i).Shapes) { if ($sh.HasTextFrame -and $sh.TextFrame.TextRange.Text -like '*public benchmarks*') { $pub = $i; break } }
    if ($pub) { break }
}
if ($pub) { Copy-Item (Join-Path $qa ("slide{0:D2}.png" -f $pub)) (Join-Path $Root 'scripts\video\cards\public.png') -Force; Write-Host "public card = slide $pub" }
$p.Close(); $pp.Quit()

if (-not $NoVideo) {
    Step 're-shoot the Bench shot (S7-S11 group) and re-assemble'
    & powershell -ExecutionPolicy Bypass -File scripts\make_video.ps1 -Only S11
    if ($LASTEXITCODE) { throw 'make_video failed' }
}
$vid = Join-Path $Root 'deliverables\video\custos_demo_720p.mp4'
if (Test-Path $vid) {
    Step 'rebuild the deck with the demo video embedded (FAQ Q4)'
    & $Py @dargs --embed-video $vid
    if ($LASTEXITCODE) { throw 'build_deck (embed) failed' }
}
Step 'source zip (fallback while the GitHub repo is private)'
& git archive --format=zip -o (Join-Path $Root 'deliverables\custos_source.zip') HEAD
Step 'done'
Get-ChildItem (Join-Path $Root 'deliverables') -Recurse -Include *.pptx, *.mp4, *.md | Format-Table FullName, Length
