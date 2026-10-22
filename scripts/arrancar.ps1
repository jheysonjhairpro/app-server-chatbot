param(
    [string]$Accion = 'servidor',
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Opciones
)

$ErrorActionPreference = 'Stop'
$env:PSModulePath = (Join-Path $PSHOME 'Modules') + ';' + $env:PSModulePath
$Root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Set-Location -LiteralPath $Root
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:ANONYMIZED_TELEMETRY = 'False'
$Logs = Join-Path $Root 'registros'
New-Item -ItemType Directory -Path $Logs -Force | Out-Null
$StateFile = Join-Path $Logs 'servicios.json'
$mutexHash = [BitConverter]::ToString(([Security.Cryptography.SHA256]::Create()).ComputeHash([Text.Encoding]::UTF8.GetBytes($Root.ToLowerInvariant()))).Replace('-', '').Substring(0, 20)
$StartupMutex = New-Object Threading.Mutex($false, ('Local\UnambaStartup_' + $mutexHash))
try { $acquired = $StartupMutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $acquired = $true }
if (!$acquired) {
    Write-Host 'Ya hay un arranque del servidor en curso. Espera a que termine.'
    $StartupMutex.Dispose()
    exit 0
}
$Managed = @{}
if (Test-Path -LiteralPath $StateFile) {
    $previous = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
    foreach ($property in $previous.PSObject.Properties) { $Managed[$property.Name] = $property.Value }
}

function Run-Program([string]$Executable, [string[]]$Arguments, [string]$InputText = '') {
    # Capturar stderr sin confundir avisos de Python con errores de PowerShell 5.
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $Executable
    $info.Arguments = (($Arguments | ForEach-Object { '"' + $_.Replace('"', '\"') + '"' }) -join ' ')
    $info.WorkingDirectory = $Root
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.RedirectStandardInput = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    try {
        [void]$process.Start()
        $output = $process.StandardOutput.ReadToEndAsync()
        $errors = $process.StandardError.ReadToEndAsync()
        if ($InputText) { $process.StandardInput.WriteLine($InputText) }
        $process.StandardInput.Close()
        $process.WaitForExit()
        return @{Code = $process.ExitCode; Out = $output.Result; Error = $errors.Result}
    } finally { $process.Dispose() }
}

function Run-Checked([string]$Executable, [string[]]$Arguments) {
    $result = Run-Program $Executable $Arguments
    if ($result.Out) { Write-Host $result.Out.TrimEnd() }
    if ($result.Code -ne 0) {
        if ($result.Error) { Write-Host $result.Error.TrimEnd() }
        throw "No termino correctamente: $([IO.Path]::GetFileName($Executable))"
    }
}

function Stop-ManagedTree([int]$ServiceId) {
    foreach ($child in @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$ServiceId" -ErrorAction SilentlyContinue)) {
        $live = Get-Process -Id $child.ProcessId -ErrorAction SilentlyContinue
        if ($live -and [Math]::Abs(($live.StartTime.ToUniversalTime() - $child.CreationDate.ToUniversalTime()).TotalSeconds) -lt 2) {
            Stop-ManagedTree $child.ProcessId
        }
    }
    Stop-Process -Id $ServiceId -ErrorAction SilentlyContinue
}

function Save-State {
    $Managed | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $StateFile -Encoding UTF8
}

function Get-Json([string]$Url, [int]$Timeout = 3000) {
    $request = [Net.WebRequest]::Create($Url)
    $request.Proxy = $null
    $request.Timeout = $Timeout
    $response = $request.GetResponse()
    try {
        $reader = New-Object IO.StreamReader($response.GetResponseStream())
        try { return ($reader.ReadToEnd() | ConvertFrom-Json) } finally { $reader.Dispose() }
    } finally { $response.Dispose() }
}

function Test-Health([string]$Url, [string]$Field, [string]$Expected = '') {
    try {
        $json = Get-Json $Url
        if ($Field -eq 'models') { return $json.PSObject.Properties.Name -contains 'models' }
        $value = $json.$Field
        if ($Expected) { return $value -eq $Expected }
        return [bool]$value
    } catch { return $false }
}

function Assert-Local([string]$Url) {
    $address = [Uri]$Url
    if ($address.Scheme -ne 'http' -or $address.Host -notin @('127.0.0.1', 'localhost', '[::1]', '::1')) {
        throw "La direccion debe ser HTTP local: $Url"
    }
}

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
        [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + $env:Path
}

function Install-Application([string]$Id) {
    if (!(Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Falta $Id y no esta disponible winget para instalarlo automaticamente. Instala App Installer de Windows y vuelve a abrir iniciar.bat."
    }
    Write-Host "Instalando $Id (solo esta primera vez)..."
    Run-Checked (Get-Command winget).Source @('install', '--id', $Id, '--exact', '--source', 'winget', '--accept-package-agreements', '--accept-source-agreements', '--silent')
    Refresh-Path
}

function Find-Python {
    $candidates = @()
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command -and $command.Source -notlike '*WindowsApps*') { $candidates += $command.Source }
    $candidates += @(Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe" -ErrorAction SilentlyContinue | ForEach-Object FullName)
    $candidates += @(Get-ChildItem "$env:LOCALAPPDATA\Python\pythoncore-*\python.exe" -ErrorAction SilentlyContinue | ForEach-Object FullName)
    foreach ($candidate in $candidates) {
        $result = Run-Program $candidate @('-c', 'import sys; assert sys.version_info >= (3,10) and sys.maxsize > 2**32; print(sys.executable)')
        if ($result.Code -eq 0) { return $result.Out.Trim() }
    }
    return $null
}

function Ensure-Python {
    $script:Python = Join-Path $Root '.venv\Scripts\python.exe'
    if (!(Test-Path -LiteralPath $Python)) {
        $base = Find-Python
        if (!$base) { Install-Application 'Python.Python.3.12'; $base = Find-Python }
        if (!$base) { throw 'No se encontro Python de 64 bits despues de la instalacion.' }
        Write-Host 'Preparando el entorno Python del proyecto...'
        Run-Checked $base @('-m', 'venv', (Join-Path $Root '.venv'))
    }
    $requirements = @('requirements.txt')
    $fingerprint = (($requirements | ForEach-Object { ([BitConverter]::ToString(([Security.Cryptography.SHA256]::Create()).ComputeHash([IO.File]::ReadAllBytes((Join-Path $Root $_))))).Replace('-', '') }) -join ':')
    $markerPath = Join-Path $Logs 'entorno_listo.json'
    $marker = $null
    if (Test-Path -LiteralPath $markerPath) { $marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json }
    $check = 'import chromadb,dotenv,flask,ollama,pypdf,pdf2image,firecrawl,playwright.sync_api,llama_index.core,llama_index.llms.ollama,llama_index.embeddings.ollama,llama_index.vector_stores.chroma'
    $ready = (Run-Program $Python @('-c', $check)).Code -eq 0
    if (!$ready -or !$marker -or $marker.hash -ne $fingerprint) {
        Write-Host 'Comprobando e instalando las dependencias necesarias...'
        Run-Checked $Python @('-m', 'pip', 'install', '--prefer-binary', '-r', (Join-Path $Root 'requirements.txt'))
        Run-Checked $Python @('-c', $check)
        @{hash = $fingerprint; python = $Python} | ConvertTo-Json | Set-Content -LiteralPath $markerPath -Encoding UTF8
    }

}

function Ensure-Browser {
    $browserCheck = 'from playwright.sync_api import sync_playwright; from pathlib import Path; p=sync_playwright().start(); ok=Path(p.chromium.executable_path).exists(); p.stop(); raise SystemExit(0 if ok else 1)'
    if ((Run-Program $Python @('-c', $browserCheck)).Code -ne 0) {
        Write-Host 'Instalando Chromium para la extraccion de documentos...'
        Run-Checked $Python @('-m', 'playwright', 'install', 'chromium')
    }
}

function Write-ViewConfig {
    $code = 'import sys,base64;from pathlib import Path;p=Path(sys.argv[1])/"api"/"servidor.local.php";v=base64.b64encode((Path(sys.argv[2])/".env").as_posix().encode()).decode();p.write_text("<?php return base64_decode("+repr(v)+");\n",encoding="utf-8")'
    Run-Checked $Python @('-c', $code, $UiRoot, $Root)
}

function Start-LocalProcess([string]$Name, [string]$Executable, [string[]]$Arguments, [string]$Folder, [string]$HealthUrl, [string]$Field, [string]$Expected = '') {
    if (Test-Health $HealthUrl $Field $Expected) {
        Write-Host "$Name ya esta disponible."
        return
    }
    if ($Managed.ContainsKey($Name) -and $Managed[$Name].url -eq $HealthUrl) {
        $record = $Managed[$Name]
        $running = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
        if ($running -and $running.Path -eq $record.executable -and $record.created -and
            [Math]::Abs(($running.StartTime.ToUniversalTime() - [DateTime]::Parse($record.created).ToUniversalTime()).TotalSeconds) -lt 2) {
            Write-Host "$Name ya esta iniciado; esperando su disponibilidad."
            return
        }
    }
    $uri = [Uri]$HealthUrl
    $listener = New-Object Net.Sockets.TcpClient
    try {
        if ($listener.ConnectAsync('127.0.0.1', $uri.Port).Wait(500) -and $listener.Connected) {
            throw "El puerto $($uri.Port) esta ocupado por otro servicio. No se cerrara ese programa."
        }
    } catch [AggregateException] { } finally { $listener.Dispose() }
    Write-Host "Iniciando $Name..."
    $quoted = @($Arguments | ForEach-Object { '"' + $_.Replace('"', '\"') + '"' })
    $outPath = Join-Path $Logs ("arranque_$Name.log")
    $errPath = Join-Path $Logs ("arranque_$Name.error.log")
    $process = Start-Process -FilePath $Executable -ArgumentList $quoted -WorkingDirectory $Folder -WindowStyle Hidden -PassThru -RedirectStandardOutput $outPath -RedirectStandardError $errPath
    $Managed[$Name] = @{pid = $process.Id; executable = $Executable; arguments = $Arguments; folder = $Folder; url = $HealthUrl; created = $process.StartTime.ToUniversalTime().ToString("o")}
    Save-State
    $deadline = [DateTime]::UtcNow.AddSeconds(150)
    do {
        if (Test-Health $HealthUrl $Field $Expected) { return }
        $process.Refresh()
        if ($process.HasExited) { throw "$Name se cerro al iniciar. Revisa $errPath" }
        Start-Sleep -Milliseconds 750
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "$Name no respondio a tiempo. Revisa $errPath"
}

function Read-AppConfig {
    $code = 'import sys,json;from motores.portal_unamba.src import config as p;from configuracion import servidor as s;from configuracion.entorno import variables,ruta;print(json.dumps({"api":"http://127.0.0.1:"+str(s.PORT),"ollama":p.OLLAMA_BASE_URL,"models":list(dict.fromkeys([p.LLM_MODEL,p.EMBED_MODEL,s.CHAT_MODEL,s.EMBEDDING_MODEL])),"token":s.TOKEN,"pdf_db":str(s.CHROMA_DIR),"pdf_collection":s.COLECCION_DOCS,"portal_db":str(p.VECTOR_STORE_DIR),"portal_collection":p.CHROMA_COLLECTION,"environment":variables(),"ui_root":str(ruta("UNAMBA_UI_DIR","C:/xampp/htdocs/app-chatbot-first-commit"))}))'
    $result = Run-Program $Python @('-') $code
    if ($result.Code -ne 0) { throw ('No se pudo cargar la configuracion: ' + $result.Error) }
    return ($result.Out | ConvertFrom-Json)
}

function Ensure-Ollama {
    Assert-Local $AppConfig.ollama
    $command = Get-Command ollama.exe -ErrorAction SilentlyContinue
    $script:Ollama = if ($command) { $command.Source } else { Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe' }
    if (!(Test-Path -LiteralPath $Ollama)) {
        Install-Application 'Ollama.Ollama'
        $script:Ollama = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
    }
    $env:OLLAMA_HOST = $AppConfig.ollama
    Start-LocalProcess 'ollama' $Ollama @('serve') $Root ($AppConfig.ollama + '/api/tags') 'models'
    $tags = (Get-Json ($AppConfig.ollama + '/api/tags')).models
    foreach ($model in $AppConfig.models) {
        $wanted = if ($model.Contains(':')) { $model } else { $model + ':latest' }
        if ($wanted -notin @($tags | ForEach-Object name)) {
            Write-Host "Descargando el modelo local $model (solo si falta)..."
            Run-Checked $Ollama @('pull', $model)
        }
    }
}

function Get-IndexCount([string]$Folder, [string]$Collection) {
    $code = 'import sys,chromadb;from pathlib import Path;p=Path(sys.argv[1]);n=0;'
    $code += "`nif (p/'chroma.sqlite3').exists():`n try:n=chromadb.PersistentClient(path=str(p),settings=chromadb.Settings(anonymized_telemetry=False)).get_collection(sys.argv[2]).count()`n except chromadb.errors.NotFoundError:pass`nprint(n)"
    $result = Run-Program $Python @('-', $Folder, $Collection) $code
    if ($result.Code -ne 0) { throw "No se pudo comprobar el indice $Collection." }
    return [int]$result.Out.Trim()
}

function Ensure-Indexes {
    $portalCount = Get-IndexCount $AppConfig.portal_db $AppConfig.portal_collection
    if ($portalCount -eq 0) {
        Write-Host 'Preparando el indice del portal con los documentos locales...'
        Run-Checked $Python @((Join-Path $PSScriptRoot 'portal.py'), 'indexar')
        $portalCount = Get-IndexCount $AppConfig.portal_db $AppConfig.portal_collection
    }
    $pdfCount = Get-IndexCount $AppConfig.pdf_db $AppConfig.pdf_collection
    if ($pdfCount -eq 0) {
        Write-Host 'Preparando el indice PDF desde el texto ya extraido...'
        $indexing = Run-Program $Python @((Join-Path $PSScriptRoot 'ingest_pdf.py'), '--solo-texto')
        if ($indexing.Out) { Write-Host $indexing.Out.TrimEnd() }
        $pdfCount = Get-IndexCount $AppConfig.pdf_db $AppConfig.pdf_collection
        if ($pdfCount -eq 0) { throw 'No hay PDF completos para indexar. Revisa datos/pdf/texto_extraido/resultado.json.' }
    }
    Write-Host "Indices disponibles: portal $portalCount fragmentos; PDF $pdfCount fragmentos."
}

try {
    if ($Accion -eq 'detener') {
        foreach ($name in @($Managed.Keys)) {
            $record = $Managed[$name]
            $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($record.pid)" -ErrorAction SilentlyContinue
            # Evitar cerrar un PID reutilizado o un proceso ajeno al arranque.
            $identity = @($record.arguments | Where-Object { $_.Contains($record.folder) } | Select-Object -First 1)
            $sameStart = $record.created -and $process -and ([Math]::Abs(($process.CreationDate.ToUniversalTime() - [DateTime]::Parse($record.created).ToUniversalTime()).TotalSeconds) -lt 2)
            if ($process -and $process.ExecutablePath -eq $record.executable -and ($sameStart -or ($identity.Count -gt 0 -and $process.CommandLine.Contains($identity[0])))) {
                Stop-ManagedTree $record.pid
                Write-Host "$name detenido."
            }
            $Managed.Remove($name)
        }
        Save-State
        exit 0
    }
    Write-Host 'UNAMBA - servidor local (Portal Web y PDF)'
    Ensure-Python
    Run-Checked $Python @('-c', 'from configuracion.entorno import preparar_env; preparar_env()')
    $AppConfig = Read-AppConfig
    foreach ($property in $AppConfig.environment.PSObject.Properties) { [Environment]::SetEnvironmentVariable($property.Name, [string]$property.Value, 'Process') }
    if ($Accion -in @('extraer', 'enlaces', 'panel')) { Ensure-Browser }
    if ($Accion -in @('chatbot', 'panel', 'indexar', 'extraer', 'limpiar', 'enlaces', 'indexar-pdf')) {
        $AppConfig = Read-AppConfig
        Ensure-Ollama
        # Una operación manual larga no debe bloquear otros arranques.
        $StartupMutex.ReleaseMutex()
        $acquired = $false
        $ErrorActionPreference = 'Continue'
        if ($Accion -eq 'indexar-pdf') { & $Python (Join-Path $PSScriptRoot 'ingest_pdf.py') --solo-texto @Opciones }
        else { & $Python (Join-Path $PSScriptRoot 'portal.py') $Accion @Opciones }
        exit $LASTEXITCODE
    }
    if ($Accion -notin @('todo', 'preparar', 'servidor')) { throw 'Usa iniciar.bat para iniciar el servidor o iniciar.bat detener para cerrarlo.' }
    Assert-Local $AppConfig.api
    Ensure-Ollama
    Ensure-Indexes
    # Apache se administra desde XAMPP. Este enlace no contiene credenciales.
    $UiRoot = $AppConfig.ui_root
    if (Test-Path -LiteralPath (Join-Path $UiRoot 'api\config.php')) {
        Write-ViewConfig
    }
    if ($Accion -eq 'preparar') { Write-Host 'Dependencias, modelos e indices preparados.'; exit 0 }
    Ensure-Browser
    Start-LocalProcess 'api' $Python @('-B', (Join-Path $Root 'servidor\app.py')) $Root ($AppConfig.api + '/salud') 'ok'
    $adminPort = if ($env:UNAMBA_ADMIN_PORT) { [int]$env:UNAMBA_ADMIN_PORT } else { 8765 }
    $panelUrl = "http://127.0.0.1:$adminPort"
    Start-LocalProcess 'panel' $Python @('-B', (Join-Path $PSScriptRoot 'portal.py'), 'panel', '--no-browser', '--port', [string]$adminPort) $Root ($panelUrl + '/api/health') 'online'
    Start-Process $panelUrl
    Write-Host ('SERVIDOR LISTO: ' + $AppConfig.api)
    Write-Host ('PANEL LISTO: ' + $panelUrl)
    Write-Host 'Portal Web y PDF disponibles. La vista se sirve con Apache desde XAMPP.'
    exit 0
} catch {
    Write-Host ("No se pudo completar el arranque: " + $_.Exception.Message) -ForegroundColor Red
    Write-Host "Detalle de los servicios: $Logs"
    exit 1
} finally {
    if ($acquired) { $StartupMutex.ReleaseMutex() }
    $StartupMutex.Dispose()
}
