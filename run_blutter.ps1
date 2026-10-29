$ErrorActionPreference = 'Continue'
$vs = 'C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat'
$envFile = 'D:\apk-analysis\genie792\vs_env.txt'
cmd /c "`"$vs`" >nul 2>&1 && set" | Out-File -Encoding ascii $envFile
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') {
        Set-Item -Path ("Env:" + $matches[1]) -Value $matches[2]
    }
}
Set-Location D:\apk-analysis\blutter
python blutter.py "D:\apk-analysis\genie792\lib\arm64-v8a" "D:\apk-analysis\genie792\blutter_out"
Write-Output ("EXITCODE=" + $LASTEXITCODE)
