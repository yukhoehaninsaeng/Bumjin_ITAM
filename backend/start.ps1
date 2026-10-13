$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    py -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.11 or newer and retry.' }
    & '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
if (-not $env:APP_PASSWORD -and -not ((Test-Path '.\data\admin_settings.json') -and ((Get-Content '.\data\admin_settings.json' -Raw | ConvertFrom-Json).password_hash))) {
    $securePassword = Read-Host 'Admin password (12+ characters)' -AsSecureString
    $passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
    try { $env:APP_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer) }
}
if (-not $env:PORT) { $env:PORT = '1880' }
if (-not $env:PUBLIC_BASE_URL) { $env:PUBLIC_BASE_URL = "http://127.0.0.1:$($env:PORT)" }
Write-Host "URL: $($env:PUBLIC_BASE_URL) / User: admin"
& '.\.venv\Scripts\python.exe' app.py
