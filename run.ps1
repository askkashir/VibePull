$ErrorActionPreference = "Stop"

$pythonCandidates = @(
  $env:PYTHON,
  "C:\Users\Sohaib\AppData\Local\Python\pythoncore-3.14-64\python.exe",
  "C:\Users\Sohaib\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe",
  "python"
) | Where-Object { $_ }

$python = $pythonCandidates | Where-Object {
  try {
    if ($_ -eq "python") {
      $cmd = Get-Command python -ErrorAction Stop
      return $cmd.Source -notlike "*\WindowsApps\python.exe"
    }
    return Test-Path $_
  } catch {
    return $false
  }
} | Select-Object -First 1

if (-not $python) {
  throw "No usable Python interpreter found. Set `$env:PYTHON to your python.exe path."
}
$url = "http://localhost:8766"

if (-not (Test-Path "indexes/text_index.faiss") -or -not (Test-Path "indexes/text_metadata.json")) {
  & $python pipeline/retrieval.py
}

if (-not (Test-Path "indexes/bm25_index.pkl")) {
  & $python pipeline/bm25_retrieval.py
}

Start-Process $url
& $python backend/server.py
