$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    py -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 이상을 설치한 뒤 다시 실행하세요.' }
    & '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw '필수 패키지 설치에 실패했습니다.' }
}
if (-not $env:APP_PASSWORD) {
    $securePassword = Read-Host '테스트 관리자 비밀번호 (12자 이상)' -AsSecureString
    $passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
    try { $env:APP_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer) }
}
Write-Host '접속: http://127.0.0.1:8080 / 사용자: admin'
& '.\.venv\Scripts\python.exe' app.py
