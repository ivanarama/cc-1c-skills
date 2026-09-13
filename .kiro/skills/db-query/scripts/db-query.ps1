# db-query v1.0 — Read-only 1C query through COMConnector
# Source: https://github.com/Nikolay-Shirokov/cc-1c-skills

[CmdletBinding(PositionalBinding=$false)]
param(
    [Parameter(Mandatory=$false)][string]$InfoBasePath,
    [Parameter(Mandatory=$false)][string]$InfoBaseServer,
    [Parameter(Mandatory=$false)][string]$InfoBaseRef,
    [Parameter(Mandatory=$false)][string]$UserName,
    [Parameter(Mandatory=$false)][string]$Password,
    [Parameter(Mandatory=$false)][string]$QueryFile,
    [Parameter(Mandatory=$false)][string]$QueryText,
    [Parameter(Mandatory=$false)][string]$ParametersJson,
    [Parameter(Mandatory=$false)][string]$OutputFile,
    [Parameter(Mandatory=$false)][ValidateSet('Json', 'Csv')][string]$OutputFormat = 'Json',
    [Parameter(Mandatory=$false)][ValidateRange(1, 1000000)][int]$MaxRows = 10000,
    [Parameter(Mandatory=$false)][string]$ComProgId
)

$OutputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'

function Fail([string]$Message) {
    Write-Host "Error: $Message" -ForegroundColor Red
    exit 1
}

function Clean-Path([string]$Value, [string]$Name) {
    if (-not $Value) { return $Value }
    $clean = $Value.Trim().Trim('"').Trim("'")
    if ($clean.Contains('"')) { Fail "$Name contains a quote character" }
    return $clean
}

function Convert-QueryValue($Value, $Connection) {
    if ($null -eq $Value) { return $null }
    if ($Value -is [string] -or $Value -is [bool] -or
        $Value -is [byte] -or $Value -is [int16] -or $Value -is [int32] -or
        $Value -is [int64] -or $Value -is [single] -or $Value -is [double] -or
        $Value -is [decimal] -or $Value -is [datetime]) { return $Value }
    if ($Value -is [__ComObject]) { return [string]$Connection.String($Value) }
    return [string]$Value
}

$InfoBasePath = Clean-Path $InfoBasePath '-InfoBasePath'
$QueryFile = Clean-Path $QueryFile '-QueryFile'
$ParametersJson = Clean-Path $ParametersJson '-ParametersJson'
$OutputFile = Clean-Path $OutputFile '-OutputFile'

$fileConnection = -not [string]::IsNullOrWhiteSpace($InfoBasePath)
$serverConnection = -not [string]::IsNullOrWhiteSpace($InfoBaseServer) -or -not [string]::IsNullOrWhiteSpace($InfoBaseRef)
if ($fileConnection -eq $serverConnection) { Fail 'specify either -InfoBasePath or both -InfoBaseServer and -InfoBaseRef' }
if ($serverConnection -and ([string]::IsNullOrWhiteSpace($InfoBaseServer) -or [string]::IsNullOrWhiteSpace($InfoBaseRef))) {
    Fail '-InfoBaseServer and -InfoBaseRef must be specified together'
}
if ($fileConnection -and -not (Test-Path -LiteralPath $InfoBasePath -PathType Container)) {
    Fail "infobase directory not found: $InfoBasePath"
}

$hasQueryFile = -not [string]::IsNullOrWhiteSpace($QueryFile)
$hasQueryText = -not [string]::IsNullOrWhiteSpace($QueryText)
if ($hasQueryFile -eq $hasQueryText) { Fail 'specify exactly one of -QueryFile or -QueryText' }
if ($hasQueryFile) {
    if (-not (Test-Path -LiteralPath $QueryFile -PathType Leaf)) { Fail "query file not found: $QueryFile" }
    $QueryText = Get-Content -LiteralPath $QueryFile -Raw -Encoding UTF8
}
if ([string]::IsNullOrWhiteSpace($QueryText)) { Fail 'query text is empty' }

$parameters = @{}
if ($ParametersJson) {
    if (-not (Test-Path -LiteralPath $ParametersJson -PathType Leaf)) { Fail "parameters file not found: $ParametersJson" }
    try { $parsed = Get-Content -LiteralPath $ParametersJson -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { Fail "invalid parameters JSON: $($_.Exception.Message)" }
    if ($null -eq $parsed -or $parsed -is [array] -or $parsed -is [string] -or $parsed -is [ValueType]) {
        Fail 'parameters JSON must contain an object'
    }
    foreach ($property in $parsed.PSObject.Properties) {
        $value = $property.Value
        if ($null -ne $value -and -not ($value -is [string]) -and -not ($value -is [ValueType])) {
            Fail "parameter '$($property.Name)' must be a scalar JSON value"
        }
        $parameters[$property.Name] = $value
    }
}

foreach ($value in @($InfoBasePath, $InfoBaseServer, $InfoBaseRef, $UserName, $Password)) {
    if ($value -and $value.Contains('"')) { Fail 'connection values cannot contain quote characters' }
}
$connectionString = if ($fileConnection) {
    "File=`"$InfoBasePath`";"
} else {
    "Srvr=`"$InfoBaseServer`";Ref=`"$InfoBaseRef`";"
}
if ($UserName) { $connectionString += "Usr=`"$UserName`";" }
if ($null -ne $Password -and $Password -ne '') { $connectionString += "Pwd=`"$Password`";" }

$connector = $null
$connection = $null
$query = $null
$result = $null
$columnsCollection = $null
$selection = $null
$columnObjects = @()

try {
    $progIds = if ($ComProgId) { @($ComProgId) } else { @('V85.COMConnector', 'V83.COMConnector') }
    $connectorErrors = @()
    foreach ($progId in $progIds) {
        try {
            $connector = New-Object -ComObject $progId
            $ComProgId = $progId
            break
        } catch {
            $connectorErrors += "${progId}: $($_.Exception.Message)"
        }
    }
    if ($null -eq $connector) { throw "COMConnector is not registered ($($connectorErrors -join '; '))" }

    $connection = $connector.Connect($connectionString)
    $query = $connection.NewObject('Query')
    $query.Text = $QueryText
    foreach ($name in @($parameters.Keys | Sort-Object)) { $query.SetParameter($name, $parameters[$name]) }

    $result = $query.Execute()
    $columnsCollection = $result.Columns()
    $columnNames = @()
    for ($index = 0; $index -lt $columnsCollection.Count(); $index++) {
        $column = $columnsCollection.Get($index)
        $columnObjects += $column
        $columnNames += [string]$column.Name()
    }

    $rows = [Collections.Generic.List[object]]::new()
    $selection = $result.Select()
    $truncated = $false
    while ($selection.Next()) {
        if ($rows.Count -ge $MaxRows) { $truncated = $true; break }
        $row = [ordered]@{}
        for ($index = 0; $index -lt $columnNames.Count; $index++) {
            $row[$columnNames[$index]] = Convert-QueryValue ($selection.Get($index)) $connection
        }
        $rows.Add([pscustomobject]$row)
    }

    if ($OutputFormat -eq 'Csv') {
        $text = if ($rows.Count -gt 0) { ($rows | ConvertTo-Csv -NoTypeInformation) -join [Environment]::NewLine } else { '' }
    } else {
        $payload = [ordered]@{
            ok = $true
            columns = $columnNames
            rowCount = $rows.Count
            truncated = $truncated
            rows = $rows
        }
        $text = $payload | ConvertTo-Json -Depth 8
    }

    if ($OutputFile) {
        $fullOutput = [IO.Path]::GetFullPath($OutputFile)
        $parent = Split-Path -Parent $fullOutput
        if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        [IO.File]::WriteAllText($fullOutput, $text + [Environment]::NewLine, [Text.UTF8Encoding]::new($true))
        Write-Host "[OK] Query returned $($rows.Count) row(s); output: $fullOutput"
        if ($truncated) { Write-Host "[WARN] Result truncated at $MaxRows row(s)" -ForegroundColor Yellow }
    } else {
        Write-Output $text
    }
} catch {
    $message = $_.Exception.Message
    if ($Password) { $message = $message.Replace($Password, '***') }
    Fail $message
} finally {
    foreach ($object in @($selection) + $columnObjects + @($columnsCollection, $result, $query, $connection, $connector)) {
        if ($null -ne $object -and $object -is [__ComObject]) {
            try { [Runtime.InteropServices.Marshal]::FinalReleaseComObject($object) | Out-Null } catch {}
        }
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
