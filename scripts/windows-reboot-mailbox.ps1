# SYSTEM observer for the disposable Windows guest. Only one fixed reboot command
# is supported. There is no general remote shell and no host forwarding port.
$ErrorActionPreference = 'Stop'
$root = 'C:\LabTrial'
$config = Get-Content -LiteralPath "$root\guest.json" -Raw | ConvertFrom-Json
function Assert-Guest {
    $serial = [string](Get-CimInstance Win32_ComputerSystemProduct).IdentifyingNumber
    if ($config.nonce -cnotmatch '^[0-9a-f]{32}$' -or $serial.Trim() -cne ('GLAB-' + $config.nonce)) { throw 'guest_identity_required' }
    if ([Security.Principal.WindowsIdentity]::GetCurrent().User.Value -ne 'S-1-5-18') { throw 'SYSTEM_required' }
}
Assert-Guest
$deadline = [DateTime]::UtcNow.AddHours(2)
$probeName = 'GLAB-Login-Probe-' + $config.nonce
$firstKick = "$root\probe-initial-started"
$headers = @{Authorization=('Bearer ' + $config.mailbox_token)}
while ([DateTime]::UtcNow -lt $deadline) {
    try {
        $operation = 'guest_identity'
        Assert-Guest
        $operation = 'guest_local_user_lookup'
        $user = Get-LocalUser -Name LabUser -ErrorAction SilentlyContinue
        if ($null -ne $user -and -not (Test-Path -LiteralPath "$root\probe-registered")) {
            # Existing SYSTEM-owned code/config stays read-only to LabUser.
            # Root-only Write creates new evidence/data; it grants neither
            # DeleteChild nor inherited write access to protected executables.
            $operation = 'guest_acl'
            $acl = New-Object Security.AccessControl.DirectorySecurity
            $acl.SetAccessRuleProtection($true, $false)
            $inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
            $none = [Security.AccessControl.PropagationFlags]::None
            foreach ($sid in @('S-1-5-18','S-1-5-32-544')) {
                $identity = New-Object Security.Principal.SecurityIdentifier($sid)
                $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($identity, 'FullControl', $inherit, $none, 'Allow'))
            }
            $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($user.SID, 'ReadAndExecute', $inherit, $none, 'Allow'))
            $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($user.SID, 'Write', 'None', $none, 'Allow'))
            # Inherited CREATOR OWNER maps to each new object's real owner.
            # LabUser can replace its own .pending/results and manage its data,
            # while pre-existing SYSTEM-owned files receive no LabUser write.
            $creator = New-Object Security.Principal.SecurityIdentifier('S-1-3-0')
            $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($creator, 'FullControl', $inherit, 'InheritOnly', 'Allow'))
            Set-Acl -LiteralPath $root -AclObject $acl
            $action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\LabTrial\probe.ps1'
            $principal = New-ScheduledTaskPrincipal -UserId $user.SID.Value -LogonType Interactive -RunLevel Limited
            $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user.SID.Value
            # Cold doctor allows15 minutes, followed by bounded startup/run
            # checks. Keep a finite cap with room for those separate budgets.
            $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::FromMinutes(30)) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
            $operation = 'guest_probe_registration'
            Register-ScheduledTask -TaskName $probeName -Action $action -Principal $principal -Settings $settings -Trigger $trigger | Out-Null
            [IO.File]::WriteAllText("$root\probe-registered", 'registered')
        }
        # Initial installation may finish after the first logon event. Start the
        # probe once in that existing interactive session, only BEFORE baseline.
        # After baseline/reboot this observer never starts any Lab/probe task.
        if ((Test-Path -LiteralPath "$root\probe-registered") -and -not (Test-Path -LiteralPath $firstKick) -and -not (Test-Path -LiteralPath "$root\baseline.json")) {
            $operation = 'guest_initial_session_lookup'
            $interactive = @(Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" | Where-Object {
                $_.SessionId -gt 0 -and (Invoke-CimMethod -InputObject $_ -MethodName GetOwnerSid).Sid -eq $user.SID.Value
            })
            if ($interactive.Count -gt 0) {
                [IO.File]::WriteAllText($firstKick, 'initial-only')
                $task = Get-ScheduledTask -TaskName $probeName
                $info = Get-ScheduledTaskInfo -TaskName $probeName
                if ($task.State -ne 'Running' -and $info.LastRunTime.Year -lt 2000) {
                    $operation = 'guest_initial_probe_start'
                    Start-ScheduledTask -TaskName $probeName
                }
            }
        }
        $operation = 'guest_evidence_read'
        $os = Get-CimInstance Win32_OperatingSystem
        $body = @{nonce=$config.nonce; boot_utc=$os.LastBootUpTime.ToUniversalTime().ToString('o'); caption=$os.Caption; product_type=[int]$os.ProductType; stage='guest_observer_running'}
        foreach ($name in @('baseline','result')) {
            $path = "$root\$name.json"
            if (Test-Path -LiteralPath $path) {
                # Bind the decoded evidence and file hash to the same bytes.
                $bytes = [IO.File]::ReadAllBytes($path)
                if ($bytes.Length -gt 131072) { throw 'guest_evidence_oversized' }
                $body[$name] = [Text.Encoding]::UTF8.GetString($bytes).TrimStart([char]0xFEFF) | ConvertFrom-Json
                $sha = [Security.Cryptography.SHA256]::Create()
                try { $body[$name + '_sha256'] = ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','').ToLowerInvariant() }
                finally { $sha.Dispose() }
            }
        }
        if (Test-Path -LiteralPath "$root\probe-output.json") {
            try { $body.probe = Get-Content -LiteralPath "$root\probe-output.json" -Raw | ConvertFrom-Json } catch { }
        }
        if (Test-Path -LiteralPath "$root\probe-registered") {
            $task = Get-ScheduledTask -TaskName $probeName
            $info = Get-ScheduledTaskInfo -TaskName $probeName
            $body.probe_task = @{state=[string]$task.State; last_result=[long]$info.LastTaskResult; last_run_utc=$info.LastRunTime.ToUniversalTime().ToString('o')}
        }
        $operation = 'guest_mailbox_poll'
        $response = Invoke-RestMethod -UseBasicParsing -Uri ($config.mailbox + '/poll') -Method Post -ContentType 'application/json' -Headers $headers -Body ($body | ConvertTo-Json -Depth 20 -Compress) -TimeoutSec 8
        if ($response.command -ceq 'reboot') {
            $operation = 'guest_reboot_precondition'
            Assert-Guest
            if ($response.nonce -cne $config.nonce -or -not (Test-Path -LiteralPath "$root\baseline.json") -or (Test-Path -LiteralPath "$root\result.json")) { throw 'reboot_precondition_failed' }
            $hash = (Get-FileHash -LiteralPath "$root\baseline.json" -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($response.baseline_sha256 -cne $hash) { throw 'reboot_baseline_changed' }
            # Exclusive durable claim: no repeated shutdown commands or retries.
            $claim = [IO.File]::Open("$root\reboot-command.claim", [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            $claim.Dispose()
            # This script is inside the owned Windows VM. The outer Linux process
            # remains alive and independently validates the changed guest boot.
            & "$env:SystemRoot\System32\shutdown.exe" /r /t 0
            exit $LASTEXITCODE
        }
    } catch {
        # Compact bounded diagnostics; never publish PowerShell exception payloads.
        $allowed = @('guest_identity_required','SYSTEM_required','guest_acl_failed','guest_evidence_oversized','reboot_precondition_failed','reboot_baseline_changed')
        $failure = if ([string]$_.Exception.Message -in $allowed) { [string]$_.Exception.Message } else { $operation + '_failed' }
        $hresult = [int]$_.Exception.HResult
        try {
            $body = @{nonce=$config.nonce; stage='guest_mailbox_retry'; error_code=$failure; hresult=$hresult} | ConvertTo-Json -Compress
            Invoke-RestMethod -UseBasicParsing -Uri ($config.mailbox + '/stage') -Method Post -ContentType 'application/json' -Headers $headers -Body $body -TimeoutSec 3 | Out-Null
        } catch { }
    }
    Start-Sleep -Seconds 4
}
