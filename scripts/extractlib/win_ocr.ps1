# Windows 내장 OCR(Windows.Media.Ocr) 래퍼.
#
# 왜 이 방식인가: 이 저장소는 외부 OCR 엔진(tesseract 등) 설치를 요구하지 않는다.
# Windows 10/11 에 한국어 OCR 팩이 이미 들어 있으므로 그것을 쓴다.
#
# 원본(wiki/questions/pdf_ocr.ps1)은 Windows.Data.Pdf 로 PDF 를 직접 렌더했지만,
# 여기서는 렌더링을 파이썬(fitz/PIL)이 맡고 이 스크립트는 "이미지 파일 -> 텍스트"만 한다.
#   - PDF 와 PNG 정답지를 같은 경로로 처리할 수 있다 (정답지가 PNG 로만 제공되는 회차가 많다)
#   - DPI 를 파이썬 쪽에서 조절할 수 있다
#
# 출력은 JSON 한 덩어리. 줄마다 bounding box 를 함께 준다.
# 2단 편집 문제지는 읽기 순서가 뒤섞이므로 x 좌표로 단(column)을 갈라야 하고,
# 그러려면 좌표가 반드시 필요하다. .Text 만 받으면 이 복구가 불가능하다.
param(
    [Parameter(Mandatory = $true)]
    [string]$ListFile   # UTF-8 텍스트 파일. 한 줄에 이미지 경로 하나.
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
[Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime] | Out-Null
[Windows.Storage.Streams.DataWriter, Windows.Storage.Streams, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType = WindowsRuntime] | Out-Null
[Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime] | Out-Null

function AwaitOp {
    param($Operation, [Type]$ResultType)
    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object { $_.Name -eq "AsTask" -and $_.IsGenericMethodDefinition -and $_.GetParameters().Count -eq 1 } |
        Select-Object -First 1
    $task = $method.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    $task.Wait()
    return $task.Result
}

# 한국어 엔진이 없으면 사용자 프로필 언어로 떨어진다. 둘 다 없으면 실패시킨다 —
# 조용히 영어로 읽어서 한글이 사라지는 것이 가장 나쁜 실패다.
$language = [Windows.Globalization.Language]::new("ko-KR")
$ocrEngine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
if ($null -eq $ocrEngine) {
    $ocrEngine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
}
if ($null -eq $ocrEngine) {
    throw "Windows OCR 엔진을 만들 수 없다. 설정 > 시간 및 언어 > 언어에서 한국어 OCR 을 설치한다."
}

$pages = New-Object System.Collections.Generic.List[object]
foreach ($path in [System.IO.File]::ReadAllLines($ListFile, [System.Text.Encoding]::UTF8)) {
    if ([string]::IsNullOrWhiteSpace($path)) { continue }
    $resolved = (Resolve-Path $path).Path
    # StorageFile.OpenReadAsync 의 반환 타입(IRandomAccessStreamWithContentType)은
    # PowerShell 의 AsTask 제네릭 해석이 실패한다. 바이트를 직접 메모리 스트림에 실어 우회한다.
    $bytes = [System.IO.File]::ReadAllBytes($resolved)
    $stream = [Windows.Storage.Streams.InMemoryRandomAccessStream]::new()
    $writer = [Windows.Storage.Streams.DataWriter]::new($stream)
    $writer.WriteBytes($bytes)
    AwaitOp ($writer.StoreAsync()) ([uint32]) | Out-Null
    $writer.DetachStream() | Out-Null
    $writer.Dispose()
    $stream.Seek(0)
    $decoder = AwaitOp ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = AwaitOp ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    if (
        $bitmap.BitmapPixelFormat -ne [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8 -or
        $bitmap.BitmapAlphaMode -ne [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied
    ) {
        $bitmap = [Windows.Graphics.Imaging.SoftwareBitmap]::Convert(
            $bitmap,
            [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
            [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied)
    }
    $result = AwaitOp ($ocrEngine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

    $lines = New-Object System.Collections.Generic.List[object]
    foreach ($line in $result.Lines) {
        $x0 = [double]::MaxValue; $y0 = [double]::MaxValue; $x1 = 0.0; $y1 = 0.0
        foreach ($word in $line.Words) {
            $r = $word.BoundingRect
            if ($r.X -lt $x0) { $x0 = $r.X }
            if ($r.Y -lt $y0) { $y0 = $r.Y }
            if (($r.X + $r.Width) -gt $x1) { $x1 = $r.X + $r.Width }
            if (($r.Y + $r.Height) -gt $y1) { $y1 = $r.Y + $r.Height }
        }
        $lines.Add([pscustomobject]@{ text = $line.Text; bbox = @($x0, $y0, $x1, $y1) })
    }
    $pages.Add([pscustomobject]@{
        path = $resolved
        width = $bitmap.PixelWidth
        height = $bitmap.PixelHeight
        lines = @($lines.ToArray())
    })
    $bitmap.Dispose()
}

# PowerShell 5.1 의 ConvertTo-Json 은 중첩된 List[object] 에서 ArgumentException 을 던진다.
# 배열로 바꿔서 넘긴다.
$json = ConvertTo-Json -InputObject @($pages.ToArray()) -Depth 6 -Compress
[Console]::Out.Write($json)
