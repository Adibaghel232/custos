# Text-to-speech per sentence with the Windows System.Speech (SAPI) voice. Input: work\shots.json. Output: work\audio\<shot>_<nn>.wav
param(
    [string]$Voice = 'Microsoft Zira Desktop',
    [int]$Rate = -1,
    [string]$ShotsFile = '',
    [string]$OutDir = ''
)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not $ShotsFile) { $ShotsFile = Join-Path $Root 'deliverables\video\work\shots.json' }
if (-not $OutDir) { $OutDir = Join-Path $Root 'deliverables\video\work\audio' }
New-Item -ItemType Directory -Force $OutDir | Out-Null
Get-ChildItem $OutDir -Filter *.wav -ErrorAction SilentlyContinue | Remove-Item -Force
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$names = $synth.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object { $_.VoiceInfo.Name }
if ($names -notcontains $Voice) { Write-Host "Voice '$Voice' not installed; using '$($names[0])'"; $Voice = $names[0] }
$synth.SelectVoice($Voice)
$synth.Rate = $Rate
$synth.Volume = 100
$shotList = Get-Content -Raw -Encoding UTF8 $ShotsFile | ConvertFrom-Json
$n = 0
foreach ($shot in $shotList) {
    $i = 0
    foreach ($s in $shot.sentences) {
        $i++
        $path = Join-Path $OutDir ('{0}_{1:00}.wav' -f $shot.id, $i)
        $synth.SetOutputToWaveFile($path)
        $synth.Speak($s.tts)
        $synth.SetOutputToNull()
        $n++
    }
}
$synth.Dispose()
Write-Host "tts: $n sentences, voice '$Voice', rate $Rate -> $OutDir"
