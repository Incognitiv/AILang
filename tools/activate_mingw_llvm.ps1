param(
    [string]$MingwBin = ""
)

$ErrorActionPreference = "Stop"

function Add-Candidate {
    param(
        [System.Collections.Generic.List[string]]$Candidates,
        [string]$Path
    )
    if ($Path -and -not $Candidates.Contains($Path)) {
        $Candidates.Add($Path)
    }
}

function Get-ToolchainBin {
    $candidates = [System.Collections.Generic.List[string]]::new()
    Add-Candidate $candidates $MingwBin
    Add-Candidate $candidates $env:AILANG_LLVM_BIN
    if ($env:MINGW_PREFIX) {
        Add-Candidate $candidates (Join-Path $env:MINGW_PREFIX "bin")
    }
    if ($env:MSYSTEM_PREFIX) {
        Add-Candidate $candidates (Join-Path $env:MSYSTEM_PREFIX "bin")
    }
    if ($env:SystemDrive) {
        $driveRoot = $env:SystemDrive.TrimEnd('\', '/') + '\'
        Add-Candidate $candidates (Join-Path $driveRoot "msys64\mingw64\bin")
        Add-Candidate $candidates (Join-Path $driveRoot "msys64\clang64\bin")
        Add-Candidate $candidates (Join-Path $driveRoot "msys64\ucrt64\bin")
    }
    if ($env:LLVM_HOME) {
        Add-Candidate $candidates (Join-Path $env:LLVM_HOME "bin")
    }
    if ($env:LLVM_ROOT) {
        Add-Candidate $candidates (Join-Path $env:LLVM_ROOT "bin")
    }
    if ($env:ProgramW6432) {
        Add-Candidate $candidates (Join-Path $env:ProgramW6432 "LLVM\bin")
    }
    if ($env:ProgramFiles) {
        Add-Candidate $candidates (Join-Path $env:ProgramFiles "LLVM\bin")
    }

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Container) {
            return $candidate
        }
    }

    $clang = Get-Command clang -ErrorAction SilentlyContinue
    if ($clang) {
        return Split-Path -Parent $clang.Source
    }

    throw "LLVM/MinGW bin directory not found. Pass -MingwBin or set AILANG_LLVM_BIN, MINGW_PREFIX, MSYSTEM_PREFIX, LLVM_HOME, or LLVM_ROOT."
}

$MingwBin = Get-ToolchainBin

if (-not (Test-Path -LiteralPath $MingwBin -PathType Container)) {
    throw "MSYS2 MinGW LLVM bin directory not found: $MingwBin"
}

$env:AILANG_LLVM_BIN = $MingwBin
$parts = @($MingwBin) + (($env:PATH -split ';') | Where-Object {
    $_ -and ($_.TrimEnd('\') -ine $MingwBin.TrimEnd('\'))
})
$env:PATH = ($parts -join ';')

Write-Host "AILANG_LLVM_BIN=$env:AILANG_LLVM_BIN"
Write-Host "clang=$((Get-Command clang -ErrorAction Stop).Source)"
Write-Host "llvm-profdata=$((Get-Command llvm-profdata -ErrorAction Stop).Source)"
