# Deterministic export of desktop/assets/icon.svg; no design/runtime dependency.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$iconRoot = Join-Path $PSScriptRoot '../desktop/assets'
function Get-IconPng([int]$size) {
    $bitmap = [Drawing.Bitmap]::new($size, $size)
    $graphics = [Drawing.Graphics]::FromImage($bitmap)
    $graphics.Clear([Drawing.Color]::Transparent)
    $graphics.ScaleTransform($size / 512.0, $size / 512.0)
    $ink = [Drawing.SolidBrush]::new([Drawing.ColorTranslator]::FromHtml('#16130E'))
    $purple = [Drawing.SolidBrush]::new([Drawing.ColorTranslator]::FromHtml('#4B3FE0'))
    $graphics.FillRectangle($ink, 68, 68, 412, 412)
    $graphics.FillRectangle($ink, 32, 32, 436, 436)
    $graphics.FillRectangle($purple, 56, 56, 388, 388)
    foreach ($left in @(148, 264)) {
        $graphics.FillRectangle([Drawing.Brushes]::White, $left, 174, 28, 138)
        $graphics.FillRectangle([Drawing.Brushes]::White, $left, 286, 90, 26)
    }
    $stream = [IO.MemoryStream]::new()
    $bitmap.Save($stream, [Drawing.Imaging.ImageFormat]::Png)
    $bytes = $stream.ToArray()
    $stream.Dispose(); $graphics.Dispose(); $bitmap.Dispose(); $ink.Dispose(); $purple.Dispose()
    return ,$bytes
}
[IO.File]::WriteAllBytes((Join-Path $iconRoot 'icon.png'), (Get-IconPng 1024))
$sizes = @(16, 24, 32, 48, 64, 128, 256)
$images = @($sizes | ForEach-Object { ,(Get-IconPng $_) })
$stream = [IO.MemoryStream]::new()
$writer = [IO.BinaryWriter]::new($stream)
$writer.Write([uint16]0); $writer.Write([uint16]1); $writer.Write([uint16]$sizes.Count)
$offset = 6 + 16 * $sizes.Count
for ($index = 0; $index -lt $sizes.Count; $index++) {
    $dimension = if ($sizes[$index] -eq 256) { 0 } else { $sizes[$index] }
    $writer.Write([byte]$dimension); $writer.Write([byte]$dimension)
    $writer.Write([uint16]0); $writer.Write([uint16]1); $writer.Write([uint16]32)
    $writer.Write([uint32]$images[$index].Length); $writer.Write([uint32]$offset)
    $offset += $images[$index].Length
}
foreach ($bytes in $images) { $writer.Write([byte[]]$bytes) }
[IO.File]::WriteAllBytes((Join-Path $iconRoot 'icon.ico'), $stream.ToArray())
$writer.Dispose(); $stream.Dispose()
