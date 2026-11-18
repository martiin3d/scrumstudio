# Build the Windows GUI launcher from the checkout. Output stays in ignored data/.
param([string]$OutputDirectory)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$output = if ($OutputDirectory) { $OutputDirectory } else { Join-Path $root 'data\desktop' }
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
}
if (-not (Test-Path -LiteralPath $compiler)) { throw '.NET Framework 4 is required to build the Windows launcher.' }
New-Item -ItemType Directory -Force -Path $output | Out-Null

Add-Type -AssemblyName System.Drawing
$bitmap = [Drawing.Bitmap]::new(48, 48)
$graphics = [Drawing.Graphics]::FromImage($bitmap)
$accent = [Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(163, 189, 255))
$iconPath = Join-Path $output 'Scrum Studio.ico'
try {
    $graphics.Clear([Drawing.Color]::FromArgb(34, 36, 42))
    $graphics.FillRectangle($accent, 9, 12, 8, 24)
    $graphics.FillRectangle($accent, 20, 12, 8, 17)
    $graphics.FillRectangle($accent, 31, 12, 8, 21)
    $icon = [Drawing.Icon]::FromHandle($bitmap.GetHicon())
    $stream = [IO.File]::Create($iconPath)
    try { $icon.Save($stream) } finally { $stream.Dispose(); $icon.Dispose() }
} finally {
    $accent.Dispose()
    $graphics.Dispose()
    $bitmap.Dispose()
}

$exe = Join-Path $output 'Scrum Studio.exe'
& $compiler /nologo /target:winexe /reference:System.Windows.Forms.dll "/win32icon:$iconPath" "/out:$exe" (Join-Path $PSScriptRoot 'ScrumStudioLauncher.cs')
if ($LASTEXITCODE -ne 0) { throw 'Windows launcher compilation failed.' }
Write-Output $exe
