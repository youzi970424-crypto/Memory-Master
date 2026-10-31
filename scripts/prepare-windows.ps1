$ErrorActionPreference = 'Stop'
$runtime = Join-Path $PSScriptRoot '../src-tauri/runtime/python'
$python = Join-Path $runtime 'python.exe'
if (!(Test-Path $python)) {
    New-Item -ItemType Directory -Force -Path $runtime | Out-Null
    $archive = Join-Path $runtime 'python.zip'
    try {
        Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip' -OutFile $archive
        Expand-Archive -LiteralPath $archive -DestinationPath $runtime -Force
    } finally {
        if (Test-Path $archive) { Remove-Item -LiteralPath $archive }
    }
}
& $python -c 'import sys, sqlite3, ssl, http.server; assert sys.version_info[:3] == (3, 12, 10); assert sys.maxsize > 2**32'
if ($LASTEXITCODE -ne 0) { throw 'Python runtime validation failed; remove src-tauri/runtime and retry.' }
