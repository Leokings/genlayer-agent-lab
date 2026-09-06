# Runs as SYSTEM during specialize in an explicitly marked disposable QEMU guest.
# No action is permitted on the developer's PC. No reboot occurs during setup.
$ErrorActionPreference = 'Stop'
$root = 'C:\LabTrial'
$config = Get-Content -LiteralPath "$root\guest.json" -Raw | ConvertFrom-Json
$serial = [string](Get-CimInstance Win32_ComputerSystemProduct).IdentifyingNumber
if ($config.nonce -cnotmatch '^[0-9a-f]{32}$' -or $serial.Trim() -cne ('GLAB-' + $config.nonce)) { throw 'guest_identity_required' }
if ([Security.Principal.WindowsIdentity]::GetCurrent().User.Value -ne 'S-1-5-18') { throw 'SYSTEM_required' }

function Send-Stage([string]$stage) {
    try {
        $body = @{nonce=$config.nonce; stage=$stage} | ConvertTo-Json -Compress
        Invoke-RestMethod -UseBasicParsing -Uri ($config.mailbox + '/stage') -Method Post -ContentType 'application/json' -Headers @{Authorization=('Bearer ' + $config.mailbox_token)} -Body $body -TimeoutSec 5 | Out-Null
    } catch { }
}
try {
    $operation = 'guest_specialize_configuration'
    Send-Stage 'guest_specialize_started'
    # Setup/network update reboots are excluded from this bounded startup trial.
    New-Item -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' -Force | Out-Null
    New-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' -Name NoAutoUpdate -PropertyType DWord -Value 1 -Force | Out-Null
    $installer = "$root\python-installer.exe"
    $operation = 'guest_python_verification'
    if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant() -cne $config.python_sha256) { throw 'python_hash_mismatch' }
    $signature = Get-AuthenticodeSignature -LiteralPath $installer
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') { throw 'python_signature_mismatch' }
    $operation = 'guest_python_installation'
    $process = Start-Process -FilePath $installer -ArgumentList @('/quiet', 'InstallAllUsers=1', 'TargetDir=C:\LabTrial\Python', 'Include_launcher=0', 'Include_test=0', 'Include_doc=0', 'AssociateFiles=0', 'Shortcuts=0', 'PrependPath=0') -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -notin @(0,3010)) { throw 'python_install_failed' }
    Send-Stage 'guest_python_installed'
    $operation = 'guest_venv_creation'
    # Windows PowerShell5.1 can turn harmless native stderr into a terminating
    # NativeCommandError under ErrorActionPreference=Stop. Judge native processes
    # by their exit status, with separate private stdout/stderr streams.
    $process = Start-Process -FilePath "$root\Python\python.exe" -ArgumentList @('-I','-m','venv',"$root\venv") -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput "$root\venv.stdout.log" -RedirectStandardError "$root\venv.stderr.log"
    if ($process.ExitCode -ne 0) { throw 'venv_failed' }
    $wheel = Join-Path $root $config.wheel_name
    $operation = 'guest_wheel_verification'
    if ((Get-FileHash -LiteralPath $wheel -Algorithm SHA256).Hash.ToLowerInvariant() -cne $config.wheel_sha256) { throw 'wheel_hash_mismatch' }
    $operation = 'guest_wheel_installation'
    $process = Start-Process -FilePath "$root\venv\Scripts\python.exe" -ArgumentList @('-I','-m','pip','--isolated','--disable-pip-version-check','install','--no-cache-dir','--index-url','https://pypi.org/simple',$wheel) -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput "$root\pip.stdout.log" -RedirectStandardError "$root\pip.stderr.log"
    if ($process.ExitCode -ne 0) { throw 'wheel_install_failed' }
    Send-Stage 'guest_wheel_installed'
    # Accounts are created by the oobeSystem LocalAccounts settings. This SYSTEM
    # observer registers the user probe once that standard account exists.
    $action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\LabTrial\mailbox.ps1'
    $principal = New-ScheduledTaskPrincipal -UserId 'S-1-5-18' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::FromHours(2)) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $operation = 'guest_mailbox_registration'
    Register-ScheduledTask -TaskName ('GLAB-Observer-' + $config.nonce) -Action $action -Principal $principal -Settings $settings -Trigger (New-ScheduledTaskTrigger -AtStartup) | Out-Null
    $operation = 'guest_mailbox_start'
    Start-ScheduledTask -TaskName ('GLAB-Observer-' + $config.nonce)
    Send-Stage 'guest_waiting_for_real_user_logon'
} catch {
    # Whitelisted stage names only; installer and third-party output stay private.
    $allowed = @('python_hash_mismatch','python_signature_mismatch','python_install_failed','venv_failed','wheel_hash_mismatch','wheel_install_failed')
    $failure = if ([string]$_.Exception.Message -in $allowed) { [string]$_.Exception.Message } else { $operation + '_failed' }
    Send-Stage $failure
    exit 1
}
