param(
    [string]$Server = "root@47.117.134.103",
    [switch]$StopRemote
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LogDir = Join-Path $Root "outputs\service-logs"

foreach ($name in @("frontend", "backend", "ssh-tunnel")) {
    $pidPath = Join-Path $LogDir "$name.pid"
    if (Test-Path $pidPath) {
        $procId = [int](Get-Content $pidPath)
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "Stopping $name pid=$procId ..."
            Stop-Process -Id $procId -Force
        }
        Remove-Item $pidPath -Force
    }
}

if ($StopRemote) {
    Write-Host "Stopping remote inference/vLLM on $Server ..."
    ssh $Server 'pkill -f "backend.inference_server:app.*--port 9000" || true; pkill -f "vllm.entrypoints.openai.api_server.*--port 8001" || true'
}

Write-Host "Stopped local stack."
