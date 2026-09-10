param(
    [string]$Server = "root@47.117.134.103",
    [int]$LocalTunnelPort = 19000,
    [int]$RemoteInferencePort = 9000,
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LogDir = Join-Path $Root "outputs\service-logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Start-LoggedProcess {
    param(
        [string]$Name,
        [string]$FilePath,
        [string]$Arguments,
        [string]$WorkingDirectory
    )

    $stdout = Join-Path $LogDir "$Name.out.log"
    $stderr = Join-Path $LogDir "$Name.err.log"
    Write-Host "Starting $Name ..."
    $proc = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $Arguments `
        -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -Path (Join-Path $LogDir "$Name.pid") -Value $proc.Id
    Write-Host "  pid=$($proc.Id), logs=$stdout"
}

function Invoke-Remote {
    param([string]$Command)
    $Command | ssh $Server "bash -s"
}

Write-Host "Starting remote vLLM on $Server ..."
Invoke-Remote @'
cd /root
mkdir -p /root/xhs-audit-agent-demo/outputs/service-logs
if ! pgrep -f "vllm.entrypoints.openai.api_server.*--port 8001" >/dev/null; then
  nohup bash -lc "source /root/vllm-019-env/bin/activate && python -m vllm.entrypoints.openai.api_server --host 127.0.0.1 --port 8001 --model /root/models/Qwen3-VL-8B-Instruct --served-model-name qwen3-vl-8b --trust-remote-code" \
    > /root/xhs-audit-agent-demo/outputs/service-logs/vllm.out.log \
    2> /root/xhs-audit-agent-demo/outputs/service-logs/vllm.err.log &
fi
'@

Write-Host "Starting remote inference server on $Server ..."
Invoke-Remote @'
cd /root/xhs-audit-agent-demo
mkdir -p outputs/service-logs
if ! pgrep -f "backend.inference_server:app.*--port 9000" >/dev/null; then
  nohup bash -lc "source .venv/bin/activate && python -m uvicorn backend.inference_server:app --host 0.0.0.0 --port 9000" \
    > outputs/service-logs/inference.out.log \
    2> outputs/service-logs/inference.err.log &
fi
'@

Start-Sleep -Seconds 2

Start-LoggedProcess `
    -Name "ssh-tunnel" `
    -FilePath "ssh" `
    -Arguments "-N -L $LocalTunnelPort`:127.0.0.1:$RemoteInferencePort $Server" `
    -WorkingDirectory $Root

Start-Sleep -Seconds 2

Start-LoggedProcess `
    -Name "backend" `
    -FilePath (Join-Path $Root ".venv\Scripts\python.exe") `
    -Arguments "-m uvicorn backend.main:app --host 127.0.0.1 --port $BackendPort" `
    -WorkingDirectory $Root

Start-LoggedProcess `
    -Name "frontend" `
    -FilePath "python" `
    -Arguments "-m http.server $FrontendPort" `
    -WorkingDirectory (Join-Path $Root "frontend")

Write-Host ""
Write-Host "Started. Open: http://127.0.0.1:$FrontendPort/"
Write-Host "Local API: http://127.0.0.1:$BackendPort"
Write-Host "Remote inference through tunnel: http://127.0.0.1:$LocalTunnelPort"
Write-Host "Logs: $LogDir"
