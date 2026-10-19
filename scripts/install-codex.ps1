param(
    [string]$ProfileRoot = [Environment]::GetFolderPath('UserProfile')
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonTool = (Get-Command python -ErrorAction Stop).Source
$codexTool = (Get-Command codex -ErrorAction Stop).Source
$skillRoot = Join-Path $ProfileRoot '.agents\skills'
$configRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $ProfileRoot '.codex' }
$configPath = Join-Path $configRoot 'config.toml'
$backupRoot = Join-Path $configRoot ('backups\scrum-studio\' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Force -Path $skillRoot, $backupRoot | Out-Null
if (Test-Path -LiteralPath $configPath) {
    Copy-Item -LiteralPath $configPath -Destination (Join-Path $backupRoot 'config.toml')
}
$skillFolders = Get-ChildItem -LiteralPath (Join-Path $repoRoot 'skills') -Directory
foreach ($folder in $skillFolders) {
    if (-not (Test-Path -LiteralPath (Join-Path $folder.FullName 'SKILL.md'))) { throw "Invalid skill: $($folder.Name)" }
    $target = Join-Path $skillRoot $folder.Name
    if (Test-Path -LiteralPath $target) {
        $savedSkills = Join-Path $backupRoot 'skills'
        New-Item -ItemType Directory -Force -Path $savedSkills | Out-Null
        Copy-Item -LiteralPath $target -Destination $savedSkills -Recurse
    }
    Copy-Item -LiteralPath $folder.FullName -Destination $skillRoot -Recurse -Force
}
$serverPath = Join-Path $repoRoot 'mcp_server.py'
& $codexTool mcp add scrum-studio -- $pythonTool $serverPath
if ($LASTEXITCODE -ne 0) { throw 'Codex MCP registration failed. Existing config backup is available.' }
$registrationText = & $codexTool mcp get scrum-studio --json
if ($LASTEXITCODE -ne 0) { throw 'Codex could not read the registered MCP.' }
$registration = $registrationText | ConvertFrom-Json
if (-not $registration.enabled -or $registration.transport.command -ne $pythonTool -or $registration.transport.args -notcontains $serverPath) {
    throw 'Registered MCP configuration differs from the intended local server.'
}
& $pythonTool (Join-Path $PSScriptRoot 'check_mcp.py') --command $registration.transport.command --server $serverPath
if ($LASTEXITCODE -ne 0) { throw 'MCP stdio connection check failed.' }
foreach ($folder in $skillFolders) {
    $installed = Join-Path (Join-Path $skillRoot $folder.Name) 'SKILL.md'
    if ((Get-FileHash -LiteralPath $installed).Hash -ne (Get-FileHash -LiteralPath (Join-Path $folder.FullName 'SKILL.md')).Hash) {
        throw "Skill copy verification failed: $($folder.Name)"
    }
}
Write-Output "Installed and verified $($skillFolders.Count) skills in $skillRoot"
Write-Output "MCP registered and stdio connection verified. Backup: $backupRoot"
Write-Output 'Skills are available on the next turn. Reopen the chat or restart Codex if it has not reloaded the new MCP tools.'
