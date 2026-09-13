# cfe-validate v1.16 — Validate 1C configuration extension structure (CFE)
# Source: https://github.com/Nikolay-Shirokov/cc-1c-skills
[CmdletBinding(PositionalBinding=$false)]
param(
	[Parameter(Mandatory, Position=0)]
	[Alias('Path')]
	[string]$ExtensionPath,

	[switch]$Detailed,

	[int]$MaxErrors = 30,

	[string]$OutFile,

	# Конфигурация-источник. Без неё проверки, требующие сравнения с основной конфигурацией,
	# пропускаются (о чём сказано в отчёте), остальные работают как раньше.
	[string]$ConfigPath
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# --- Resolve path ---
if (-not [System.IO.Path]::IsPathRooted($ExtensionPath)) {
	$ExtensionPath = Join-Path (Get-Location).Path $ExtensionPath
}

if (Test-Path $ExtensionPath -PathType Container) {
	$candidate = Join-Path $ExtensionPath "Configuration.xml"
	if (Test-Path $candidate) {
		$ExtensionPath = $candidate
	} else {
		Write-Host "[ERROR] No Configuration.xml found in directory: $ExtensionPath"
		exit 1
	}
}

if (-not (Test-Path $ExtensionPath)) {
	Write-Host "[ERROR] File not found: $ExtensionPath"
	exit 1
}

$resolvedPath = (Resolve-Path $ExtensionPath).Path
$configDir = Split-Path $resolvedPath -Parent

# --- Output infrastructure ---
$script:errors = 0
$script:warnings = 0
$script:okCount = 0
$script:stopped = $false
$script:output = New-Object System.Text.StringBuilder 8192

function Out-Line {
	param([string]$msg)
	$script:output.AppendLine($msg) | Out-Null
}

function Report-OK {
	param([string]$msg)
	$script:okCount++
	if ($Detailed) { Out-Line "[OK]    $msg" }
}

function Report-Error {
	param([string]$msg)
	$script:errors++
	Out-Line "[ERROR] $msg"
	if ($script:errors -ge $MaxErrors) {
		$script:stopped = $true
	}
}

function Report-Warn {
	param([string]$msg)
	$script:warnings++
	Out-Line "[WARN]  $msg"
}

$script:dlIdentPattern = '[A-Za-z\u0410-\u042F\u0401\u0430-\u044F\u0451_][A-Za-z0-9\u0410-\u042F\u0401\u0430-\u044F\u0451_]*'

function Get-DlText {
	param($Node, [string]$Name)
	$child = $Node.SelectSingleNode("*[local-name()='$Name']")
	if ($child) { return $child.InnerText.Trim() }
	return ''
}

function Get-DlQueryParameters {
	param([string]$Query)
	$result = New-Object System.Collections.Generic.List[string]; $seen = @{}
	foreach ($match in [regex]::Matches("$Query", '&(' + $script:dlIdentPattern + ')')) {
		$name = $match.Groups[1].Value; $key = $name.ToLowerInvariant()
		if (-not $seen.ContainsKey($key)) { $seen[$key] = $true; $result.Add($name) }
	}
	return @($result)
}

function Split-DlQueryItems {
	param([string]$Text)
	$result = New-Object System.Collections.Generic.List[string]
	$current = New-Object System.Text.StringBuilder; $depth = 0; $inString = $false
	for ($i = 0; $i -lt $Text.Length; $i++) {
		$ch = $Text[$i]
		if ($ch -eq '"') {
			[void]$current.Append($ch)
			if ($inString -and $i + 1 -lt $Text.Length -and $Text[$i + 1] -eq '"') { [void]$current.Append($Text[++$i]); continue }
			$inString = -not $inString
		} elseif (-not $inString -and $ch -eq '(') { $depth++; [void]$current.Append($ch)
		} elseif (-not $inString -and $ch -eq ')') { $depth = [Math]::Max(0, $depth - 1); [void]$current.Append($ch)
		} elseif (-not $inString -and $depth -eq 0 -and $ch -eq ',') {
			$item = $current.ToString().Trim(); if ($item) { $result.Add($item) }; [void]$current.Clear()
		} else { [void]$current.Append($ch) }
	}
	$item = $current.ToString().Trim(); if ($item) { $result.Add($item) }
	return @($result)
}

function Get-DlAggregateKeyInfo {
	param([string]$Query)
	$empty = [pscustomobject]@{ GroupAliases = @(); AggregateAliases = @() }
	$aggregatePattern = '(?i)\b(?:СУММА|КОЛИЧЕСТВО|СРЕДНЕЕ|МИНИМУМ|МАКСИМУМ|SUM|COUNT|AVG|MIN|MAX)\s*\('
	if ($Query -notmatch $aggregatePattern -or $Query -match '(?i)\b(?:ОБЪЕДИНИТЬ|UNION)\b') { return $empty }
	$select = [regex]::Match($Query, '(?is)\b(?:ВЫБРАТЬ|SELECT)\b(.*?)\b(?:ИЗ|FROM)\b')
	$group = [regex]::Match($Query, '(?is)\b(?:СГРУППИРОВАТЬ\s+ПО|GROUP\s+BY)\b(.*?)(?=\b(?:ИМЕЮЩИЕ|HAVING|УПОРЯДОЧИТЬ\s+ПО|ORDER\s+BY|ИТОГИ|TOTALS)\b|$)')
	if (-not $select.Success -or -not $group.Success) { return $empty }
	$exprAlias = @{}; $aggregateAliases = @{}
	$aliasPattern = '(?is)^(.*?)\s+(?:КАК|AS)\s+(' + $script:dlIdentPattern + ')\s*$'
	foreach ($item in (Split-DlQueryItems $select.Groups[1].Value)) {
		$aliasMatch = [regex]::Match($item, $aliasPattern)
		if ($aliasMatch.Success) { $expr = $aliasMatch.Groups[1].Value; $alias = $aliasMatch.Groups[2].Value }
		else { $simple = [regex]::Match($item, '(' + $script:dlIdentPattern + ')\s*$'); if (-not $simple.Success) { continue }; $expr = $item; $alias = $simple.Groups[1].Value }
		$key = ([regex]::Replace($expr, '\s+', '')).ToLowerInvariant(); $exprAlias[$key] = $alias
		if ($expr -match $aggregatePattern) { $aggregateAliases[$alias.ToLowerInvariant()] = $true }
	}
	$groupAliases = @{}
	foreach ($expr in (Split-DlQueryItems $group.Groups[1].Value)) {
		$key = ([regex]::Replace($expr, '\s+', '')).ToLowerInvariant()
		if ($exprAlias.ContainsKey($key)) { $groupAliases[$exprAlias[$key].ToLowerInvariant()] = $true }
		elseif ($expr.Trim() -match ('^' + $script:dlIdentPattern + '$')) { $groupAliases[$expr.Trim().ToLowerInvariant()] = $true }
		else { return [pscustomobject]@{ GroupAliases = @(); AggregateAliases = @($aggregateAliases.Keys) } }
	}
	return [pscustomobject]@{ GroupAliases = @($groupAliases.Keys); AggregateAliases = @($aggregateAliases.Keys) }
}

function Remove-DlBslComments {
	param([string]$Text)
	$result = New-Object System.Collections.Generic.List[string]
	foreach ($line in ([regex]::Split("$Text", '\r?\n'))) {
		$out = New-Object System.Text.StringBuilder; $inString = $false
		for ($i = 0; $i -lt $line.Length; $i++) {
			$ch = $line[$i]
			if ($ch -eq '"') {
				[void]$out.Append($ch)
				if ($inString -and $i + 1 -lt $line.Length -and $line[$i + 1] -eq '"') { [void]$out.Append($line[++$i]); continue }
				$inString = -not $inString
			} elseif (-not $inString -and $ch -eq '/' -and $i + 1 -lt $line.Length -and $line[$i + 1] -eq '/') { break }
			else { [void]$out.Append($ch) }
		}
		$result.Add($out.ToString())
	}
	return ($result -join "`n")
}

function Get-DlBslRoutines {
	param([string]$Text)
	$clean = Remove-DlBslComments $Text; $routines = @{}
	$pattern = '(?ims)^\s*(?:Процедура|Procedure|Функция|Function)\s+(' + $script:dlIdentPattern + ')\s*\([^)]*\)(.*?)^\s*(?:КонецПроцедуры|EndProcedure|КонецФункции|EndFunction)\b'
	foreach ($match in [regex]::Matches($clean, $pattern)) {
		$name = $match.Groups[1].Value; $routines[$name.ToLowerInvariant()] = [pscustomobject]@{ Name = $name; Body = $match.Groups[2].Value }
	}
	return $routines
}

function Get-DlReachableBsl {
	param($Routines, [string]$Handler)
	$queue = New-Object System.Collections.Generic.Queue[string]; $queue.Enqueue($Handler.ToLowerInvariant())
	$seen = @{}; $bodies = New-Object System.Collections.Generic.List[string]
	while ($queue.Count -gt 0) {
		$name = $queue.Dequeue(); if ($seen.ContainsKey($name) -or -not $Routines.ContainsKey($name)) { continue }
		$seen[$name] = $true; $body = $Routines[$name].Body; $bodies.Add($body)
		foreach ($calledKey in $Routines.Keys) {
			$called = $Routines[$calledKey].Name
			if (-not $seen.ContainsKey($calledKey) -and $body -match ('(?i)(?<![A-Za-z0-9_А-Яа-яЁё])' + [regex]::Escape($called) + '\s*\(')) { $queue.Enqueue($calledKey) }
		}
	}
	return ($bodies -join "`n")
}

function Get-DlServerRoutineNames {
	param([string]$Text)
	$clean = Remove-DlBslComments $Text; $result = @{}
	$pattern = '(?im)^\s*&\s*(?:НаСервере(?:БезКонтекста)?|AtServer(?:NoContext)?)\s*$\s*^\s*(?:Процедура|Procedure|Функция|Function)\s+(' + $script:dlIdentPattern + ')\s*\('
	foreach ($match in [regex]::Matches($clean, $pattern)) { $result[$match.Groups[1].Value.ToLowerInvariant()] = $true }
	return $result
}

function Test-DlRoutineReachesServer {
	param($Routines, [string]$Name, $ServerNames, $Memo, $Visiting)
	$key = $Name.ToLowerInvariant()
	if ($Memo.ContainsKey($key)) { return [bool]$Memo[$key] }
	if ($ServerNames.ContainsKey($key)) { $Memo[$key] = $true; return $true }
	if (-not $Routines.ContainsKey($key)) { $Memo[$key] = $false; return $false }
	if ($Visiting.ContainsKey($key)) { return $false }
	$nextVisiting = @{}; foreach ($visitedKey in $Visiting.Keys) { $nextVisiting[$visitedKey] = $true }; $nextVisiting[$key] = $true
	$body = $Routines[$key].Body
	foreach ($calledKey in $Routines.Keys) {
		$calledName = $Routines[$calledKey].Name
		if ($body -match ('(?i)(?<![A-Za-z0-9_А-Яа-яЁё])' + [regex]::Escape($calledName) + '\s*\(') -and
			(Test-DlRoutineReachesServer $Routines $calledKey $ServerNames $Memo $nextVisiting)) {
			$Memo[$key] = $true; return $true
		}
	}
	$Memo[$key] = $false; return $false
}

function Get-DlFirstServerCallPosition {
	param($Routines, [string]$Handler, $ServerNames)
	$key = $Handler.ToLowerInvariant(); if (-not $Routines.ContainsKey($key)) { return $null }
	$body = $Routines[$key].Body; $memo = @{}; $positions = New-Object System.Collections.Generic.List[int]
	foreach ($calledKey in $Routines.Keys) {
		if (-not (Test-DlRoutineReachesServer $Routines $calledKey $ServerNames $memo @{})) { continue }
		$calledName = $Routines[$calledKey].Name
		$pattern = '(?i)(?<![A-Za-z0-9_А-Яа-яЁё])' + [regex]::Escape($calledName) + '\s*\('
		foreach ($match in [regex]::Matches($body, $pattern)) { $positions.Add($match.Index) }
	}
	if ($positions.Count -eq 0) { return $null }
	return ($positions | Measure-Object -Minimum).Minimum
}

function Test-DlDisabledEarlyReturn {
	param([string]$Body, [string]$FlagName, [int]$FirstServerCall)
	$flag = '(?:ЭтаФорма\s*\.\s*|ThisForm\s*\.\s*)?' + [regex]::Escape($FlagName)
	$condition = '(?:(?:Не|Not)\s+' + $flag + '|' + $flag + '\s*=\s*(?:Ложь|False))'
	$guardPattern = '(?is)\b(?:Если|If)\s+' + $condition + '\s+(?:Тогда|Then)\b(.*?)\b(?:КонецЕсли|EndIf)\b'
	foreach ($match in [regex]::Matches("$Body", $guardPattern)) {
		if ($match.Index -ge $FirstServerCall) { continue }
		$hasReturn = [regex]::IsMatch($match.Groups[1].Value, '(?i)\b(?:Возврат|Return)\s*;')
		if ($hasReturn -and ($match.Index + $match.Length) -le $FirstServerCall) { return $true }
	}
	return $false
}

function Get-DlToggleHandlers {
	param($RootNode, $Routines, $BoundTables)
	$controlledNames = @{}
	foreach ($table in $BoundTables) {
		$node = $table
		while ($node -and $node -ne $RootNode) {
			if ($node.NodeType -eq 'Element' -and $node.HasAttribute('name')) { $controlledNames[$node.GetAttribute('name').ToLowerInvariant()] = $true }
			$node = $node.ParentNode
		}
	}
	$result = @()
	foreach ($checkbox in $RootNode.SelectNodes("*[local-name()='ChildItems']//*[local-name()='CheckBoxField']")) {
		$dataPath = $checkbox.SelectSingleNode("*[local-name()='DataPath']"); if (-not $dataPath -or -not $dataPath.InnerText.Trim()) { continue }
		$flagName = $dataPath.InnerText.Trim()
		foreach ($event in $checkbox.SelectNodes("*[local-name()='Events']/*[local-name()='Event']")) {
			$eventName = $event.GetAttribute('name').ToLowerInvariant(); if ($eventName -ne 'onchange' -and $eventName -ne 'приизменении') { continue }
			$handler = $event.InnerText.Trim(); $handlerKey = $handler.ToLowerInvariant(); if (-not $Routines.ContainsKey($handlerKey)) { continue }
			$body = $Routines[$handlerKey].Body; $controlsVisibility = $false
			foreach ($elementName in $controlledNames.Keys) {
				$pattern = '(?i)(?:Элементы|Items)\s*\.\s*' + [regex]::Escape($elementName) + '\s*\.\s*(?:Видимость|Visible)\s*='
				if ($body -match $pattern) { $controlsVisibility = $true; break }
			}
			if ($controlsVisibility) { $result += [pscustomobject]@{ FlagName = $flagName; Handler = $handler; Body = $body } }
		}
	}
	return @($result)
}

function Test-DlHidden {
	param($Node, $RootNode)
	while ($Node -and $Node -ne $RootNode) {
		$visible = $Node.SelectSingleNode("*[local-name()='Visible']")
		if ($visible -and $visible.InnerText.Trim().ToLowerInvariant() -eq 'false') { return $true }
		$Node = $Node.ParentNode
	}
	return $false
}

function Test-DlSetter {
	param([string]$Text, [string]$AttributeName, [string]$ParameterName)
	$pattern = '(?i)(?<![A-Za-z0-9_А-Яа-яЁё])(?:ЭтаФорма\s*\.\s*|ThisForm\s*\.\s*)?' + [regex]::Escape($AttributeName) +
		'\s*\.\s*(?:Параметры|Parameters)\s*\.\s*(?:УстановитьЗначениеПараметра|SetParameterValue)\s*\(\s*"' + [regex]::Escape($ParameterName) + '"'
	return [regex]::IsMatch("$Text", $pattern)
}

$finalize = {
	$checks = $script:okCount + $script:errors + $script:warnings
	if ($script:errors -eq 0 -and $script:warnings -eq 0 -and -not $Detailed) {
		$result = "=== Validation OK: Extension.$objName ($checks checks) ==="
	} else {
		Out-Line ""
		Out-Line "=== Result: $($script:errors) errors, $($script:warnings) warnings ($checks checks) ==="
		$result = $script:output.ToString()
	}
	Write-Host $result

	if ($OutFile) {
		$utf8Bom = New-Object System.Text.UTF8Encoding $true
		[System.IO.File]::WriteAllText($OutFile, $result, $utf8Bom)
		Write-Host "Written to: $OutFile"
	}
}

# --- Format version ---
# Проверенный диапазон версий формата выгрузки: 2.17 (8.3.24) … 2.21 (8.5). Полная лестница —
# docs/1c-configuration-spec.md, «Лестница версий». Версию задаёт платформа ВЫГРУЗКИ, а не режим
# совместимости конфигурации. Версии ниже 2.17 (платформы 8.3.23 и старше) существуют, но навыки
# на них не проверялись — это предупреждение о непокрытии, а не о некорректности файла.
$formatVerifiedMin = "2.17"
$formatVerifiedMax = "2.21"
# Версия формата как число: "2.20" → 220. Строковое сравнение неверно ("2.9" > "2.17").
function Get-FormatRank([string]$ver) {
	if ($ver -match '^(\d+)\.(\d+)$') { return [int]$Matches[1] * 100 + [int]$Matches[2] }
	return 0
}

# --- Reference tables ---
# Модули заимствованных объектов: тип → виды модулей. Имя свойства в <xr:PropertyState>
# совпадает с базовым именем файла модуля. Копия таблицы есть в cfe-borrow (навыки автономны).
$moduleKindsByType = @{
	"CommonModule"=@("Module"); "HTTPService"=@("Module"); "WebService"=@("Module")
	"Catalog"=@("ObjectModule","ManagerModule"); "Document"=@("ObjectModule","ManagerModule")
	"Report"=@("ObjectModule","ManagerModule"); "DataProcessor"=@("ObjectModule","ManagerModule")
	"ExchangePlan"=@("ObjectModule","ManagerModule")
	"ChartOfCharacteristicTypes"=@("ObjectModule","ManagerModule")
	"ChartOfAccounts"=@("ObjectModule","ManagerModule")
	"ChartOfCalculationTypes"=@("ObjectModule","ManagerModule")
	"BusinessProcess"=@("ObjectModule","ManagerModule"); "Task"=@("ObjectModule","ManagerModule")
	"InformationRegister"=@("RecordSetModule","ManagerModule")
	"AccumulationRegister"=@("RecordSetModule","ManagerModule")
	"AccountingRegister"=@("RecordSetModule","ManagerModule")
	"CalculationRegister"=@("RecordSetModule","ManagerModule")
	"Sequence"=@("RecordSetModule","ManagerModule")
	"Constant"=@("ValueManagerModule","ManagerModule")
	"Enum"=@("ManagerModule"); "DocumentJournal"=@("ManagerModule")
	"FilterCriterion"=@("ManagerModule")
}

$guidPattern ='^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
$identPattern = '^[A-Za-z\u0410-\u042F\u0401\u0430-\u044F\u0451_][A-Za-z0-9\u0410-\u042F\u0401\u0430-\u044F\u0451_]*$'

# 7 fixed ClassIds for Configuration
$validClassIds = @(
	"9cd510cd-abfc-11d4-9434-004095e12fc7",
	"9fcd25a0-4822-11d4-9414-008048da11f9",
	"e3687481-0a87-462c-a166-9f34594f9bba",
	"9de14907-ec23-4a07-96f0-85521cb6b53b",
	"51f2d5d8-ea4d-4064-8892-82951750031e",
	"e68182ea-4237-4383-967f-90c1e3370bc7",
	"fb282519-d103-4dd3-bc12-cb271d631dfc"
)

# 46 types in canonical order
$childObjectTypes = @(
	"Language","Subsystem","StyleItem","Style",
	"CommonPicture","SessionParameter","Role","CommonTemplate",
	"FilterCriterion","CommonModule","CommonAttribute","ExchangePlan",
	"XDTOPackage","WebService","HTTPService","WSReference",
	"EventSubscription","ScheduledJob","SettingsStorage","FunctionalOption",
	"FunctionalOptionsParameter","DefinedType","Bot","PaletteColor","CommonCommand","CommandGroup",
	"Constant","CommonForm","Catalog","Document",
	"DocumentNumerator","Sequence","DocumentJournal","Enum",
	"Report","DataProcessor","InformationRegister","AccumulationRegister",
	"ChartOfCharacteristicTypes","ChartOfAccounts","AccountingRegister",
	"ChartOfCalculationTypes","CalculationRegister",
	"BusinessProcess","Task","ExternalDataSource","IntegrationService"
)

# Type -> directory mapping
$childTypeDirMap = @{
	"Language"="Languages"; "Subsystem"="Subsystems"; "StyleItem"="StyleItems"; "Style"="Styles"; "Bot"="Bots"; "PaletteColor"="PaletteColors"
	"CommonPicture"="CommonPictures"; "SessionParameter"="SessionParameters"; "Role"="Roles"
	"CommonTemplate"="CommonTemplates"; "FilterCriterion"="FilterCriteria"; "CommonModule"="CommonModules"
	"CommonAttribute"="CommonAttributes"; "ExchangePlan"="ExchangePlans"; "XDTOPackage"="XDTOPackages"
	"WebService"="WebServices"; "HTTPService"="HTTPServices"; "WSReference"="WSReferences"
	"EventSubscription"="EventSubscriptions"; "ScheduledJob"="ScheduledJobs"
	"SettingsStorage"="SettingsStorages"; "FunctionalOption"="FunctionalOptions"
	"FunctionalOptionsParameter"="FunctionalOptionsParameters"; "DefinedType"="DefinedTypes"
	"CommonCommand"="CommonCommands"; "CommandGroup"="CommandGroups"; "Constant"="Constants"
	"CommonForm"="CommonForms"; "Catalog"="Catalogs"; "Document"="Documents"
	"DocumentNumerator"="DocumentNumerators"; "Sequence"="Sequences"
	"DocumentJournal"="DocumentJournals"; "Enum"="Enums"; "Report"="Reports"
	"DataProcessor"="DataProcessors"; "InformationRegister"="InformationRegisters"
	"AccumulationRegister"="AccumulationRegisters"
	"ChartOfCharacteristicTypes"="ChartsOfCharacteristicTypes"
	"ChartOfAccounts"="ChartsOfAccounts"; "AccountingRegister"="AccountingRegisters"
	"ChartOfCalculationTypes"="ChartsOfCalculationTypes"
	"CalculationRegister"="CalculationRegisters"
	"BusinessProcess"="BusinessProcesses"; "Task"="Tasks"
	"ExternalDataSource"="ExternalDataSources"; "IntegrationService"="IntegrationServices"
}

# Наборы GeneratedType по типу объекта (эталон — таблица §2.5 спецификации конфигурации).
# Неполный набор в заимствованной оболочке платформа отвергает при загрузке: «отсутствует один
# или более типов объекта <Тип>». Типы, у которых GeneratedType нет вовсе (общие модули,
# подписки, регламентные задания и т.п.), в карте отсутствуют — для них проверка не выполняется.
$generatedTypeCategories = @{
	"Catalog"                    = @("Object","Ref","Selection","List","Manager")
	"Document"                   = @("Object","Ref","Selection","List","Manager")
	"Enum"                       = @("Ref","Manager","List")
	"Constant"                   = @("Manager","ValueManager","ValueKey")
	"Report"                     = @("Object","Manager")
	"DataProcessor"              = @("Object","Manager")
	"ExchangePlan"               = @("Object","Ref","Selection","List","Manager")
	"Task"                       = @("Object","Ref","Selection","List","Manager")
	"BusinessProcess"            = @("Object","Ref","Selection","List","Manager","RoutePointRef")
	"ChartOfCharacteristicTypes" = @("Object","Ref","Selection","List","Manager","Characteristic")
	"ChartOfAccounts"            = @("Object","Ref","Selection","List","Manager","ExtDimensionTypes","ExtDimensionTypesRow")
	"ChartOfCalculationTypes"    = @("Object","Ref","Selection","List","Manager","DisplacingCalculationTypes","DisplacingCalculationTypesRow","BaseCalculationTypes","BaseCalculationTypesRow","LeadingCalculationTypes","LeadingCalculationTypesRow")
	"InformationRegister"        = @("Record","Manager","Selection","List","RecordSet","RecordKey","RecordManager")
	"AccumulationRegister"       = @("Record","Manager","Selection","List","RecordSet","RecordKey")
	"AccountingRegister"         = @("Record","Manager","Selection","List","RecordSet","RecordKey","ExtDimensions")
	"CalculationRegister"        = @("Record","Manager","Selection","List","RecordSet","RecordKey","Recalcs")
	"DocumentJournal"            = @("Selection","List","Manager")
	"Sequence"                   = @("Record","Manager","RecordSet")
	"FilterCriterion"            = @("Manager","List")
	"SettingsStorage"            = @("Manager")
	"ExternalDataSource"         = @("Manager","TablesManager","CubesManager")
	"IntegrationService"         = @("Manager")
	"WSReference"                = @("Manager")
	"DefinedType"                = @("DefinedType")
}

# Стандартные реквизиты объектов: в ChildObjects их нет, но пути Объект.<Стандартный> законны.
# Имена зависят от варианта встроенного языка, поэтому держим оба написания.
$script:standardObjectFields = @(
	"Code","Description","Ref","Parent","Owner","DeletionMark","Predefined","IsFolder","LineNumber",
	"Number","Date","Posted","PredefinedDataName","RegisterRecords","DataVersion","RowsCount",
	"Код","Наименование","Ссылка","Родитель","Владелец","ПометкаУдаления","Предопределенный",
	"ЭтоГруппа","НомерСтроки","Номер","Дата","Проведен","ИмяПредопределенныхДанных",
	"Движения","ВерсияДанных","КоличествоСтрок"
)

# Valid enum values for extension properties
$validEnumValues = @{
	"ConfigurationExtensionCompatibilityMode" = @("DontUse","Version8_1","Version8_2_13","Version8_2_16","Version8_3_1","Version8_3_2","Version8_3_3","Version8_3_4","Version8_3_5","Version8_3_6","Version8_3_7","Version8_3_8","Version8_3_9","Version8_3_10","Version8_3_11","Version8_3_12","Version8_3_13","Version8_3_14","Version8_3_15","Version8_3_16","Version8_3_17","Version8_3_18","Version8_3_19","Version8_3_20","Version8_3_21","Version8_3_22","Version8_3_23","Version8_3_24","Version8_3_25","Version8_3_26","Version8_3_27","Version8_3_28","Version8_5_1")
	"DefaultRunMode" = @("ManagedApplication","OrdinaryApplication","Auto")
	"ScriptVariant" = @("Russian","English")
	"InterfaceCompatibilityMode" = @("Version8_2","Version8_2EnableTaxi","Taxi","TaxiEnableVersion8_2","TaxiEnableVersion8_5","Version8_5EnableTaxi","Version8_5")
}

# --- 1. Parse XML ---
Out-Line ""

$xmlDoc = $null
try {
	$xmlDoc = New-Object System.Xml.XmlDocument
	$xmlDoc.PreserveWhitespace = $false
	$xmlDoc.Load($resolvedPath)
} catch {
	Out-Line "=== Validation: Extension (parse failed) ==="
	Out-Line ""
	Report-Error "1. XML parse failed: $($_.Exception.Message)"
	& $finalize
	exit 1
}

# --- Register namespaces ---
$ns = New-Object System.Xml.XmlNamespaceManager($xmlDoc.NameTable)
$ns.AddNamespace("md", "http://v8.1c.ru/8.3/MDClasses")
$ns.AddNamespace("v8", "http://v8.1c.ru/8.1/data/core")
$ns.AddNamespace("xr", "http://v8.1c.ru/8.3/xcf/readable")
$ns.AddNamespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
$ns.AddNamespace("xs", "http://www.w3.org/2001/XMLSchema")
$ns.AddNamespace("app", "http://v8.1c.ru/8.2/managed-application/core")

$root = $xmlDoc.DocumentElement

# --- Check 1: Root structure ---
$check1Ok = $true
$expectedNs = "http://v8.1c.ru/8.3/MDClasses"

if ($root.LocalName -ne "MetaDataObject") {
	Report-Error "1. Root element is '$($root.LocalName)', expected 'MetaDataObject'"
	& $finalize
	exit 1
}

if ($root.NamespaceURI -ne $expectedNs) {
	Report-Error "1. Root namespace is '$($root.NamespaceURI)', expected '$expectedNs'"
	$check1Ok = $false
}

$version = $root.GetAttribute("version")
$versionRank = Get-FormatRank $version
if (-not $version) {
	Report-Warn "1. Missing version attribute on MetaDataObject"
} elseif ($versionRank -eq 0) {
	Report-Error "1. Malformed version '$version' (expected N.N)"
} elseif ($versionRank -lt (Get-FormatRank $formatVerifiedMin)) {
	Report-Warn "1. Format version '$version' is below the tested range $formatVerifiedMin-$formatVerifiedMax — skills were not verified on it"
} elseif ($versionRank -gt (Get-FormatRank $formatVerifiedMax)) {
	Report-Warn "1. Format version '$version' is above the tested range $formatVerifiedMin-$formatVerifiedMax — skills were not verified on it"
}

# Must have Configuration child
$cfgNode = $null
foreach ($child in $root.ChildNodes) {
	if ($child.NodeType -eq 'Element' -and $child.LocalName -eq "Configuration" -and $child.NamespaceURI -eq $expectedNs) {
		$cfgNode = $child; break
	}
}

if (-not $cfgNode) {
	Report-Error "1. No <Configuration> element found inside MetaDataObject"
	& $finalize
	exit 1
}

# UUID
$cfgUuid = $cfgNode.GetAttribute("uuid")
if (-not $cfgUuid) {
	Report-Error "1. Missing uuid on <Configuration>"
	$check1Ok = $false
} elseif ($cfgUuid -notmatch $guidPattern) {
	Report-Error "1. Invalid uuid '$cfgUuid' on <Configuration>"
	$check1Ok = $false
}

# Get name early for header
$propsNode = $cfgNode.SelectSingleNode("md:Properties", $ns)
$nameNode = if ($propsNode) { $propsNode.SelectSingleNode("md:Name", $ns) } else { $null }
$objName = if ($nameNode -and $nameNode.InnerText) { $nameNode.InnerText } else { "(unknown)" }

$script:output.Insert(0, "=== Validation: Extension.$objName ===$([Environment]::NewLine)") | Out-Null

if ($check1Ok) {
	Report-OK "1. Root structure: MetaDataObject/Configuration, version $version"
}

if ($script:stopped) { & $finalize; exit 1 }

# --- Check 2: InternalInfo ---
$internalInfo = $cfgNode.SelectSingleNode("md:InternalInfo", $ns)
$check2Ok = $true

if (-not $internalInfo) {
	Report-Error "2. InternalInfo: missing"
} else {
	$contained = $internalInfo.SelectNodes("xr:ContainedObject", $ns)
	if ($contained.Count -ne 7) {
		Report-Warn "2. InternalInfo: expected 7 ContainedObject, found $($contained.Count)"
	}

	$foundClassIds = @{}
	foreach ($co in $contained) {
		$classId = $co.SelectSingleNode("xr:ClassId", $ns)
		$objectId = $co.SelectSingleNode("xr:ObjectId", $ns)

		if (-not $classId -or -not $classId.InnerText) {
			Report-Error "2. ContainedObject missing ClassId"
			$check2Ok = $false
			continue
		}

		$cid = $classId.InnerText
		if ($validClassIds -notcontains $cid) {
			Report-Error "2. Unknown ClassId: $cid"
			$check2Ok = $false
		}

		if ($foundClassIds.ContainsKey($cid)) {
			Report-Error "2. Duplicate ClassId: $cid"
			$check2Ok = $false
		}
		$foundClassIds[$cid] = $true

		if (-not $objectId -or -not $objectId.InnerText) {
			Report-Error "2. ContainedObject missing ObjectId for ClassId $cid"
			$check2Ok = $false
		} elseif ($objectId.InnerText -notmatch $guidPattern) {
			Report-Error "2. Invalid ObjectId '$($objectId.InnerText)' for ClassId $cid"
			$check2Ok = $false
		}
	}

	$missingIds = @($validClassIds | Where-Object { -not $foundClassIds.ContainsKey($_) })
	if ($missingIds.Count -gt 0) {
		Report-Warn "2. Missing ClassIds: $($missingIds.Count) of 7"
	}

	if ($check2Ok) {
		Report-OK "2. InternalInfo: $($contained.Count) ContainedObject, all ClassIds valid"
	}
}

if ($script:stopped) { & $finalize; exit 1 }

# --- Check 3: Extension-specific properties ---
if (-not $propsNode) {
	Report-Error "3. Properties block missing"
} else {
	$check3Ok = $true

	# ObjectBelonging = Adopted
	$obNode = $propsNode.SelectSingleNode("md:ObjectBelonging", $ns)
	if (-not $obNode -or $obNode.InnerText -ne "Adopted") {
		Report-Error "3. ObjectBelonging must be 'Adopted', got '$($obNode.InnerText)'"
		$check3Ok = $false
	}

	# Name
	if (-not $nameNode -or -not $nameNode.InnerText) {
		Report-Error "3. Name is missing or empty"
		$check3Ok = $false
	} else {
		$nameVal = $nameNode.InnerText
		if ($nameVal -notmatch $identPattern) {
			Report-Error "3. Name '$nameVal' is not a valid 1C identifier"
			$check3Ok = $false
		}
	}

	# ConfigurationExtensionPurpose
	$purposeNode = $propsNode.SelectSingleNode("md:ConfigurationExtensionPurpose", $ns)
	$validPurposes = @("Patch","Customization","AddOn")
	if (-not $purposeNode -or -not $purposeNode.InnerText) {
		Report-Error "3. ConfigurationExtensionPurpose is missing"
		$check3Ok = $false
	} elseif ($validPurposes -notcontains $purposeNode.InnerText) {
		Report-Error "3. ConfigurationExtensionPurpose '$($purposeNode.InnerText)' invalid (expected: Patch, Customization, AddOn)"
		$check3Ok = $false
	}

	# NamePrefix
	$prefixNode = $propsNode.SelectSingleNode("md:NamePrefix", $ns)
	if (-not $prefixNode -or -not $prefixNode.InnerText) {
		Report-Warn "3. NamePrefix is empty"
	}

	# KeepMappingToExtendedConfigurationObjectsByIDs
	$keepMapNode = $propsNode.SelectSingleNode("md:KeepMappingToExtendedConfigurationObjectsByIDs", $ns)
	if (-not $keepMapNode) {
		Report-Warn "3. KeepMappingToExtendedConfigurationObjectsByIDs is missing"
	}

	# DefaultLanguage
	$defLangNode = $propsNode.SelectSingleNode("md:DefaultLanguage", $ns)
	$defLang = if ($defLangNode -and $defLangNode.InnerText) { $defLangNode.InnerText } else { "" }

	if ($check3Ok) {
		$purposeVal = if ($purposeNode) { $purposeNode.InnerText } else { "?" }
		$prefixVal = if ($prefixNode -and $prefixNode.InnerText) { $prefixNode.InnerText } else { "(empty)" }
		Report-OK "3. Extension properties: Name=`"$objName`", Purpose=$purposeVal, Prefix=$prefixVal"
	}
}

if ($script:stopped) { & $finalize; exit 1 }

# --- Check 4: Enum property values ---
if ($propsNode) {
	$enumChecked = 0
	$check4Ok = $true

	foreach ($propName in $validEnumValues.Keys) {
		$propNode = $propsNode.SelectSingleNode("md:$propName", $ns)
		if ($propNode -and $propNode.InnerText) {
			$val = $propNode.InnerText
			$allowed = $validEnumValues[$propName]
			if ($allowed -notcontains $val) {
				Report-Error "4. Property '$propName' has invalid value '$val'"
				$check4Ok = $false
			}
			$enumChecked++
		}
	}

	if ($check4Ok) {
		Report-OK "4. Property values: $enumChecked enum properties checked"
	}
} else {
	Report-Warn "4. No Properties block to check"
}

if ($script:stopped) { & $finalize; exit 1 }

# --- Check 5: ChildObjects — valid types, no duplicates, order ---
$childObjNode = $cfgNode.SelectSingleNode("md:ChildObjects", $ns)

if (-not $childObjNode) {
	Report-Error "5. ChildObjects block missing"
} else {
	$check5Ok = $true
	$totalCount = 0
	$script:childObjectIndex = @{}
	$duplicates = @{}
	$typeFirstIndex = @{}
	$lastTypeOrder = -1
	$orderOk = $true

	foreach ($child in $childObjNode.ChildNodes) {
		if ($child.NodeType -ne 'Element') { continue }
		$typeName = $child.LocalName
		$objNameVal = $child.InnerText

		$typeIdx = $childObjectTypes.IndexOf($typeName)
		if ($typeIdx -lt 0) {
			Report-Error "5. Unknown type '$typeName' in ChildObjects"
			$check5Ok = $false
		} else {
			if (-not $typeFirstIndex.ContainsKey($typeName)) {
				$typeFirstIndex[$typeName] = $typeIdx
				if ($typeIdx -lt $lastTypeOrder) {
					Report-Warn "5. Type '$typeName' is out of canonical order (after type at position $lastTypeOrder)"
					$orderOk = $false
				}
				$lastTypeOrder = $typeIdx
			}
		}

		if (-not $script:childObjectIndex.ContainsKey($typeName)) { $script:childObjectIndex[$typeName] = @{} }
		if ($script:childObjectIndex[$typeName].ContainsKey($objNameVal)) {
			if (-not $duplicates.ContainsKey("$typeName.$objNameVal")) {
				Report-Error "5. Duplicate: $typeName.$objNameVal"
				$duplicates["$typeName.$objNameVal"] = $true
				$check5Ok = $false
			}
		} else {
			$script:childObjectIndex[$typeName][$objNameVal] = $true
		}

		$totalCount++
	}

	$typeCount = $script:childObjectIndex.Count
	if ($check5Ok) {
		$orderInfo = if ($orderOk) { ", order correct" } else { "" }
		Report-OK "5. ChildObjects: $typeCount types, $totalCount objects${orderInfo}"
	}
}

if ($script:stopped) { & $finalize; exit 1 }

# --- Check 6: DefaultLanguage references existing Language in ChildObjects ---
if ($defLang -and $childObjNode) {
	$langName = $defLang
	if ($langName.StartsWith("Language.")) {
		$langName = $langName.Substring(9)
	}

	$found = $false
	foreach ($child in $childObjNode.ChildNodes) {
		if ($child.NodeType -eq 'Element' -and $child.LocalName -eq "Language" -and $child.InnerText -eq $langName) {
			$found = $true; break
		}
	}

	if ($found) {
		Report-OK "6. DefaultLanguage `"$defLang`" found in ChildObjects"
	} else {
		Report-Error "6. DefaultLanguage `"$defLang`" not found in ChildObjects"
	}
} else {
	if (-not $defLang) {
		Report-Warn "6. Cannot check DefaultLanguage (empty)"
	} else {
		Report-Warn "6. Cannot check DefaultLanguage (no ChildObjects)"
	}
}

if ($script:stopped) { & $finalize; exit 1 }

# --- Check 7: Language files exist ---
if ($childObjNode) {
	$langNames = @()
	foreach ($child in $childObjNode.ChildNodes) {
		if ($child.NodeType -eq 'Element' -and $child.LocalName -eq "Language") {
			$langNames += $child.InnerText
		}
	}

	if ($langNames.Count -gt 0) {
		$existCount = 0
		foreach ($ln in $langNames) {
			$langFile = Join-Path (Join-Path $configDir "Languages") "$ln.xml"
			if (Test-Path $langFile) {
				$existCount++
			} else {
				Report-Warn "7. Language file missing: Languages/$ln.xml"
			}
		}
		if ($existCount -eq $langNames.Count) {
			Report-OK "7. Language files: $existCount/$($langNames.Count) exist"
		}
	} else {
		Report-Warn "7. No Language entries in ChildObjects"
	}
} else {
	Report-Warn "7. Cannot check language files (no ChildObjects)"
}

if ($script:stopped) { & $finalize; exit 1 }

# --- Check 8: Object directories exist ---
if ($childObjNode) {
	$dirsToCheck = @{}
	foreach ($child in $childObjNode.ChildNodes) {
		if ($child.NodeType -ne 'Element') { continue }
		$typeName = $child.LocalName
		if ($typeName -eq "Language") { continue }
		if ($childTypeDirMap.ContainsKey($typeName)) {
			$dirName = $childTypeDirMap[$typeName]
			if (-not $dirsToCheck.ContainsKey($dirName)) {
				$dirsToCheck[$dirName] = 0
			}
			$dirsToCheck[$dirName] = $dirsToCheck[$dirName] + 1
		}
	}

	$missingDirs = @()
	foreach ($dir in $dirsToCheck.Keys) {
		$dirPath = Join-Path $configDir $dir
		if (-not (Test-Path $dirPath -PathType Container)) {
			$missingDirs += "$dir ($($dirsToCheck[$dir]) objects)"
		}
	}

	if ($missingDirs.Count -eq 0) {
		Report-OK "8. Object directories: $($dirsToCheck.Count) directories, all exist"
	} else {
		foreach ($md in $missingDirs) {
			Report-Warn "8. Missing directory: $md"
		}
	}
}

if ($script:stopped) { & $finalize; exit 1 }

# --- Check 9: Borrowed objects validation + Check 10: Sub-items ---
$script:enumValuesIndex = @{}
$script:borrowedTSIndex = @{}
$script:formList = @()

# Helper: check if sub-item has explicit borrowed metadata
function Test-BorrowedSubItem {
	param($subItem, $nsm)
	$subProps = $subItem.SelectSingleNode("md:Properties", $nsm)
	if (-not $subProps) { return $false }
	$subOb = $subProps.SelectSingleNode("md:ObjectBelonging", $nsm)
	if ($subOb -and $subOb.InnerText) { return $true }
	$subExt = $subProps.SelectSingleNode("md:ExtendedConfigurationObject", $nsm)
	return [bool]($subExt -and $subExt.InnerText)
}

# Helper: validate a borrowed Attribute/EnumValue sub-item
function Validate-BorrowedSubItem {
	param([string]$checkNum, [string]$context, [string]$subType, $subItem, $nsm)
	$subProps = $subItem.SelectSingleNode("md:Properties", $nsm)
	if (-not $subProps) {
		Report-Error "${checkNum}. ${context}: ${subType} missing Properties"
		return $false
	}
	$ok = $true
	$subOb = $subProps.SelectSingleNode("md:ObjectBelonging", $nsm)
	if (-not $subOb -or $subOb.InnerText -ne "Adopted") {
		Report-Error "${checkNum}. ${context}: ${subType} ObjectBelonging must be 'Adopted'"
		$ok = $false
	}
	$subName = $subProps.SelectSingleNode("md:Name", $nsm)
	if (-not $subName -or -not $subName.InnerText) {
		Report-Error "${checkNum}. ${context}: ${subType} missing Name"
		$ok = $false
	}
	$subExt = $subProps.SelectSingleNode("md:ExtendedConfigurationObject", $nsm)
	if (-not $subExt -or -not $subExt.InnerText) {
		Report-Error "${checkNum}. ${context}: ${subType}.$($subName.InnerText) missing ExtendedConfigurationObject"
		$ok = $false
	} elseif ($subExt.InnerText -notmatch $guidPattern) {
		Report-Error "${checkNum}. ${context}: ${subType}.$($subName.InnerText) invalid ExtendedConfigurationObject"
		$ok = $false
	}
	return $ok
}

if ($childObjNode) {
	$borrowedCount = 0
	$borrowedOk = 0
	$check9Ok = $true
	$check10Ok = $true
	$subItemCount = 0

	foreach ($child in $childObjNode.ChildNodes) {
		if ($child.NodeType -ne 'Element') { continue }
		$typeName = $child.LocalName
		$childName = $child.InnerText
		if ($typeName -eq "Language") { continue }

		if (-not $childTypeDirMap.ContainsKey($typeName)) { continue }
		$dirName = $childTypeDirMap[$typeName]
		$objFile = Join-Path (Join-Path $configDir $dirName) "$childName.xml"

		if (-not (Test-Path $objFile)) { continue }

		# Parse object XML
		$objDoc = $null
		try {
			$objDoc = New-Object System.Xml.XmlDocument
			$objDoc.PreserveWhitespace = $false
			$objDoc.Load($objFile)
		} catch {
			Report-Warn "9. Cannot parse $dirName/$childName.xml: $($_.Exception.Message)"
			continue
		}

		$objNs = New-Object System.Xml.XmlNamespaceManager($objDoc.NameTable)
		$objNs.AddNamespace("md", "http://v8.1c.ru/8.3/MDClasses")
		$objNs.AddNamespace("xr", "http://v8.1c.ru/8.3/xcf/readable")

		# Find the object element (Catalog, Document, etc.)
		$objRoot = $objDoc.DocumentElement
		$objEl = $null
		foreach ($c in $objRoot.ChildNodes) {
			if ($c.NodeType -eq 'Element') { $objEl = $c; break }
		}
		if (-not $objEl) { continue }

		$objProps = $objEl.SelectSingleNode("md:Properties", $objNs)
		if (-not $objProps) { continue }

		# --- Check 9: ObjectBelonging + ExtendedConfigurationObject ---
		$obNode = $objProps.SelectSingleNode("md:ObjectBelonging", $objNs)
		if ($obNode -and $obNode.InnerText -eq "Adopted") {
			$borrowedCount++

			$extObj = $objProps.SelectSingleNode("md:ExtendedConfigurationObject", $objNs)
			if (-not $extObj -or -not $extObj.InnerText) {
				Report-Error "9. Borrowed ${typeName}.${childName}: missing ExtendedConfigurationObject"
				$check9Ok = $false
			} elseif ($extObj.InnerText -notmatch $guidPattern) {
				Report-Error "9. Borrowed ${typeName}.${childName}: invalid ExtendedConfigurationObject UUID '$($extObj.InnerText)'"
				$check9Ok = $false
			} else {
				$borrowedOk++
			}

			# Полнота набора GeneratedType: платформа отвергает оболочку с неполным набором
			# («отсутствует один или более типов объекта ChartOfCharacteristicTypes»)
			$expectedCats = $generatedTypeCategories[$typeName]
			if ($expectedCats) {
				$objInfo = $objEl.SelectSingleNode("md:InternalInfo", $objNs)
				$foundCats = @{}
				if ($objInfo) {
					foreach ($gt in $objInfo.SelectNodes("xr:GeneratedType", $objNs)) {
						$cat = $gt.GetAttribute("category")
						if ($cat) { $foundCats[$cat] = $true }
					}
				}
				$missingCats = @($expectedCats | Where-Object { -not $foundCats.ContainsKey($_) })
				if ($missingCats.Count -gt 0) {
					Report-Error "9. Borrowed ${typeName}.${childName}: missing GeneratedType categor$(if ($missingCats.Count -eq 1) { 'y' } else { 'ies' }) $($missingCats -join ', ')"
					$check9Ok = $false
				}
			}
		}

		# --- Check 10: Sub-items (Attribute, TabularSection, EnumValue, Form) ---
		$objChildObjects = $objEl.SelectSingleNode("md:ChildObjects", $objNs)
		if ($objChildObjects) {
			$ctx = "${typeName}.${childName}"
			foreach ($subItem in $objChildObjects.ChildNodes) {
				if ($subItem.NodeType -ne 'Element') { continue }
				$subType = $subItem.LocalName

				if ($subType -eq "Attribute") {
					if (-not (Test-BorrowedSubItem $subItem $objNs)) { continue }
					$subItemCount++
					if (-not (Validate-BorrowedSubItem "10" $ctx "Attribute" $subItem $objNs)) {
						$check10Ok = $false
					}
				}
				elseif ($subType -eq "TabularSection") {
					if (-not (Test-BorrowedSubItem $subItem $objNs)) { continue }
					$subItemCount++
					if (-not (Validate-BorrowedSubItem "10" $ctx "TabularSection" $subItem $objNs)) {
						$check10Ok = $false
					} else {
						# Check InternalInfo GeneratedTypes
						$tsInfo = $subItem.SelectSingleNode("md:InternalInfo", $objNs)
						$tsName = $subItem.SelectSingleNode("md:Properties/md:Name", $objNs)
						$tsLabel = if ($tsName) { $tsName.InnerText } else { "?" }
						# Индекс заимствованных ТЧ — по нему Check 12 сверяет <AdditionalColumns table="Объект.X">
						if ($tsName) {
							$tsKey = "${typeName}.${childName}"
							if (-not $script:borrowedTSIndex.ContainsKey($tsKey)) { $script:borrowedTSIndex[$tsKey] = @{} }
							$script:borrowedTSIndex[$tsKey][$tsName.InnerText] = $true
						}
						if (-not $tsInfo) {
							Report-Error "10. ${ctx}: TabularSection.${tsLabel} missing InternalInfo"
							$check10Ok = $false
						} else {
							$gtNodes = $tsInfo.SelectNodes("xr:GeneratedType", $objNs)
							$hasTSCat = $false; $hasTSRCat = $false
							foreach ($gt in $gtNodes) {
								$cat = $gt.GetAttribute("category")
								if ($cat -eq "TabularSection") { $hasTSCat = $true }
								if ($cat -eq "TabularSectionRow") { $hasTSRCat = $true }
							}
							if (-not $hasTSCat -or -not $hasTSRCat) {
								Report-Error "10. ${ctx}: TabularSection.${tsLabel} missing GeneratedType (need TabularSection + TabularSectionRow)"
								$check10Ok = $false
							}
						}
						# Recurse into TS ChildObjects/Attribute
						$tsChildObjs = $subItem.SelectSingleNode("md:ChildObjects", $objNs)
						if ($tsChildObjs) {
							foreach ($tsAttr in $tsChildObjs.ChildNodes) {
								if ($tsAttr.NodeType -ne 'Element' -or $tsAttr.LocalName -ne "Attribute") { continue }
								if (-not (Test-BorrowedSubItem $tsAttr $objNs)) { continue }
								$subItemCount++
								if (-not (Validate-BorrowedSubItem "10" "${ctx}.ТЧ.${tsLabel}" "Attribute" $tsAttr $objNs)) {
									$check10Ok = $false
								}
							}
						}
					}
				}
				elseif ($subType -eq "EnumValue" -and $typeName -eq "Enum") {
					if (-not (Test-BorrowedSubItem $subItem $objNs)) { continue }
					$subItemCount++
					if (Validate-BorrowedSubItem "10" $ctx "EnumValue" $subItem $objNs) {
						$evName = $subItem.SelectSingleNode("md:Properties/md:Name", $objNs)
						if ($evName -and $evName.InnerText) {
							if (-not $script:enumValuesIndex.ContainsKey($childName)) {
								$script:enumValuesIndex[$childName] = @{}
							}
							$script:enumValuesIndex[$childName][$evName.InnerText] = $true
						}
					} else {
						$check10Ok = $false
					}
				}
				elseif ($subType -eq "Form") {
					$formName = $subItem.InnerText
					if ($formName) {
						$formMetaFile = Join-Path (Join-Path (Join-Path (Join-Path $configDir $dirName) $childName) "Forms") "${formName}.xml"
						if (-not (Test-Path $formMetaFile)) {
							Report-Error "10. ${ctx}: Form.${formName} metadata file missing"
							$check10Ok = $false
						}
						$script:formList += @{
							TypeName = $typeName; ObjName = $childName
							FormName = $formName; DirName = $dirName
						}
						$subItemCount++
					}
				}
			}
		}

		if ($script:stopped) { break }
	}

	if ($borrowedCount -eq 0) {
		Report-OK "9. Borrowed objects: none found"
	} elseif ($check9Ok) {
		Report-OK "9. Borrowed objects: $borrowedOk/$borrowedCount validated"
	}

	if ($subItemCount -eq 0) {
		Report-OK "10. Sub-items: none found"
	} elseif ($check10Ok) {
		Report-OK "10. Sub-items: $subItemCount validated (Attributes, TabularSections, EnumValues, Forms)"
	}
}

if ($script:stopped) { & $finalize; exit 1 }

# --- Check 11: Borrowed form structure ---
$script:borrowedFormsWithTree = @()
$check11Ok = $true
$formCount = 0

foreach ($fi in $script:formList) {
	$formCount++
	$formBase = Join-Path (Join-Path (Join-Path (Join-Path $configDir $fi.DirName) $fi.ObjName) "Forms") $fi.FormName
	$formMetaFile = Join-Path (Split-Path $formBase -Parent) "$($fi.FormName).xml"
	$formXmlFile = Join-Path (Join-Path $formBase "Ext") "Form.xml"
	$moduleBslFile = Join-Path (Join-Path (Join-Path $formBase "Ext") "Form") "Module.bsl"
	$ctx = "$($fi.TypeName).$($fi.ObjName).Form.$($fi.FormName)"

	# Validate form metadata XML
	if (Test-Path $formMetaFile) {
		try {
			$fmDoc = New-Object System.Xml.XmlDocument
			$fmDoc.PreserveWhitespace = $false
			$fmDoc.Load($formMetaFile)
			$fmNs = New-Object System.Xml.XmlNamespaceManager($fmDoc.NameTable)
			$fmNs.AddNamespace("md", "http://v8.1c.ru/8.3/MDClasses")

			$fmEl = $null
			foreach ($c in $fmDoc.DocumentElement.ChildNodes) {
				if ($c.NodeType -eq 'Element') { $fmEl = $c; break }
			}
			if ($fmEl) {
				$fmProps = $fmEl.SelectSingleNode("md:Properties", $fmNs)
				if ($fmProps) {
					$fmOb = $fmProps.SelectSingleNode("md:ObjectBelonging", $fmNs)
					$isBorrowed = $fmOb -and $fmOb.InnerText -eq "Adopted"
					if ($isBorrowed) {
						$fmExt = $fmProps.SelectSingleNode("md:ExtendedConfigurationObject", $fmNs)
						if (-not $fmExt -or $fmExt.InnerText -notmatch $guidPattern) {
							Report-Error "11. ${ctx}: invalid/missing ExtendedConfigurationObject"
							$check11Ok = $false
						}
					}
					$fmType = $fmProps.SelectSingleNode("md:FormType", $fmNs)
					if ($fmType -and $fmType.InnerText -ne "Managed") {
						Report-Error "11. ${ctx}: FormType must be 'Managed', got '$($fmType.InnerText)'"
						$check11Ok = $false
					}
				}
			}
		} catch {
			Report-Warn "11. ${ctx}: Cannot parse metadata: $($_.Exception.Message)"
		}
	}

	# Form.xml must exist
	if (-not (Test-Path $formXmlFile)) {
		Report-Error "11. ${ctx}: Ext/Form.xml missing"
		$check11Ok = $false
		continue
	}

	# Module.bsl should exist
	if (-not (Test-Path $moduleBslFile)) {
		Report-Warn "11. ${ctx}: Ext/Form/Module.bsl missing"
	}

	# Read Form.xml as raw text for BaseForm checks
	$formRawText = [System.IO.File]::ReadAllText($formXmlFile, [System.Text.Encoding]::UTF8)
	# Анализируем только корневой слой формы. BaseForm — снимок заимствованной формы, его списки
	# нельзя считать добавлениями расширения и нельзя диагностировать второй раз.
	try {
		$formDoc15 = New-Object System.Xml.XmlDocument
		$formDoc15.PreserveWhitespace = $false
		$formDoc15.LoadXml($formRawText)
		$formRoot15 = $formDoc15.DocumentElement
	} catch {
		Report-Error "11. ${ctx}: Ext/Form.xml parse failed: $($_.Exception.Message)"
		$check11Ok = $false
		continue
	}

	$baseForm15 = $formRoot15.SelectSingleNode("*[local-name()='BaseForm']")
	$baseAttrNames15 = @{}
	if ($baseForm15) {
		foreach ($baseAttr in $baseForm15.SelectNodes("*[local-name()='Attributes']/*[local-name()='Attribute']")) {
			$name = $baseAttr.GetAttribute('name'); if ($name) { $baseAttrNames15[$name.ToLowerInvariant()] = $true }
		}
	}
	$moduleText15 = if (Test-Path $moduleBslFile) { [System.IO.File]::ReadAllText($moduleBslFile, [System.Text.Encoding]::UTF8) } else { '' }
	$routines15 = Get-DlBslRoutines $moduleText15
	$serverRoutineNames15 = Get-DlServerRoutineNames $moduleText15
	$allRoutineText15 = (($routines15.Values | ForEach-Object { $_.Body }) -join "`n")
	$checkedToggleHandlers15 = @{}
	$onCreateHandlers15 = @()
	$events15 = $formRoot15.SelectSingleNode("*[local-name()='Events']")
	if ($events15) {
		foreach ($event in $events15.SelectNodes("*[local-name()='Event']")) {
			$eventName = $event.GetAttribute('name').ToLowerInvariant()
			if (($eventName -eq 'oncreateatserver' -or $eventName -eq 'присозданиинасервере') -and $event.InnerText.Trim()) { $onCreateHandlers15 += $event.InnerText.Trim() }
		}
	}
	$onCreateText15 = (($onCreateHandlers15 | ForEach-Object { Get-DlReachableBsl $routines15 $_ }) -join "`n")

	foreach ($attr in $formRoot15.SelectNodes("*[local-name()='Attributes']/*[local-name()='Attribute']")) {
		$attrName = $attr.GetAttribute('name')
		$typeValues = @($attr.SelectNodes("*[local-name()='Type']/*[local-name()='Type']") | ForEach-Object { $_.InnerText.Trim() })
		if ($typeValues -notcontains 'cfg:DynamicList') { continue }
		$settings = $attr.SelectSingleNode("*[local-name()='Settings']")
		if (-not $settings -or (Get-DlText $settings 'ManualQuery').ToLowerInvariant() -ne 'true') { continue }
		$query = Get-DlText $settings 'QueryText'
		if ($query -match '(?m)^\s*\|') {
			Report-Error "11. ${ctx}: DynamicList '$attrName' QueryText contains BSL string-literal '|' prefixes"; $check11Ok = $false
		}
		$mainTable = Get-DlText $settings 'MainTable'
		$keyType = Get-DlText $settings 'KeyType'
		$keyFields = @($settings.SelectNodes("*[local-name()='KeyField']") | ForEach-Object { $_.InnerText.Trim() } | Where-Object { $_ })
		$keyFolded = @($keyFields | ForEach-Object { $_.ToLowerInvariant() })
		if (-not $mainTable) {
			if (-not $keyType) { Report-Error "11. ${ctx}: DynamicList '$attrName' without MainTable needs KeyType"; $check11Ok = $false }
			elseif (@('RowKey','FieldValue','RowNumber','Auto') -notcontains $keyType) { Report-Error "11. ${ctx}: DynamicList '$attrName' has unsupported KeyType '$keyType'"; $check11Ok = $false }
			elseif (($keyType -eq 'RowKey' -or $keyType -eq 'FieldValue') -and $keyFields.Count -eq 0) { Report-Error "11. ${ctx}: DynamicList '$attrName' KeyType=$keyType needs KeyField"; $check11Ok = $false }
			elseif ($keyType -eq 'FieldValue' -and $keyFields.Count -ne 1) { Report-Error "11. ${ctx}: DynamicList '$attrName' KeyType=FieldValue needs exactly one KeyField"; $check11Ok = $false }
			elseif ($keyType -eq 'RowNumber' -and $keyFields.Count -gt 0) { Report-Warn "11. ${ctx}: DynamicList '$attrName' KeyField is ignored for KeyType=RowNumber" }

			$duplicateKeys = @($keyFolded | Group-Object | Where-Object Count -gt 1 | ForEach-Object Name | Sort-Object)
			if ($duplicateKeys.Count -gt 0) { Report-Error "11. ${ctx}: DynamicList '$attrName' has duplicate KeyField(s): $($duplicateKeys -join ', ')"; $check11Ok = $false }
			$explicitFields = @{}
			foreach ($value in $settings.SelectNodes("*[local-name()='Field']/*[local-name()='dataPath']")) {
				$fieldName = $value.InnerText.Trim(); if ($fieldName) { $explicitFields[$fieldName.ToLowerInvariant()] = $true }
			}
			$unknownKeys = @($keyFields | Where-Object { $explicitFields.Count -gt 0 -and -not $explicitFields.ContainsKey($_.ToLowerInvariant()) })
			if ($unknownKeys.Count -gt 0) { Report-Error "11. ${ctx}: DynamicList '$attrName' KeyField(s) absent from declared fields: $($unknownKeys -join ', ')"; $check11Ok = $false }

			$keyInfo = Get-DlAggregateKeyInfo $query; $keySet = @{}; foreach ($key in $keyFolded) { $keySet[$key] = $true }
			$missingGroupKeys = @($keyInfo.GroupAliases | Where-Object { -not $keySet.ContainsKey($_) } | Sort-Object)
			if ($missingGroupKeys.Count -gt 0) { Report-Warn "11. ${ctx}: DynamicList '$attrName' aggregate key omits GROUP BY field(s) $($missingGroupKeys -join ', '); row uniqueness is not guaranteed" }
			$unstableKeys = @($keyInfo.AggregateAliases | Where-Object { $keySet.ContainsKey($_) } | Sort-Object)
			if ($unstableKeys.Count -gt 0) { Report-Warn "11. ${ctx}: DynamicList '$attrName' uses aggregate result field(s) as KeyField ($($unstableKeys -join ', ')); the row key changes with the aggregate" }
		}
		if ($mainTable -match '^AccumulationRegister\.[^.]+\.Balance$') {
			Report-Warn "11. ${ctx}: virtual accumulation-register table is used as MainTable; prefer query + RowKey/KeyField and no MainTable for an added list"
		}

		$queryParams = @(Get-DlQueryParameters $query)
		$isAdded = $baseForm15 -and -not $baseAttrNames15.ContainsKey($attrName.ToLowerInvariant())
		if (-not $isAdded -or $queryParams.Count -eq 0) { continue }
		$boundTables = @()
		foreach ($table in $formRoot15.SelectNodes("*[local-name()='ChildItems']//*[local-name()='Table']")) {
			if ((Get-DlText $table 'DataPath').ToLowerInvariant() -eq $attrName.ToLowerInvariant()) { $boundTables += $table }
		}
		if ($boundTables.Count -eq 0) { continue }
		foreach ($toggleHandler in @(Get-DlToggleHandlers $formRoot15 $routines15 $boundTables)) {
			$handlerKey = $toggleHandler.Handler.ToLowerInvariant()
			if ($checkedToggleHandlers15.ContainsKey($handlerKey)) { continue }
			$checkedToggleHandlers15[$handlerKey] = $true
			$firstServerCall = Get-DlFirstServerCallPosition $routines15 $toggleHandler.Handler $serverRoutineNames15
			if ($null -ne $firstServerCall -and -not (Test-DlDisabledEarlyReturn $toggleHandler.Body $toggleHandler.FlagName $firstServerCall)) {
				Report-Warn "11. ${ctx}: DynamicList '$attrName' checkbox handler '$($toggleHandler.Handler)' can reach a local server routine before a recognized disabled-state guard for '$($toggleHandler.FlagName)'; use ``If Not <flag> Then ... Return; EndIf`` before the server-bound call"
			}
			if ($null -ne $firstServerCall) {
				$flagPattern = '(?:ЭтаФорма\s*\.\s*|ThisForm\s*\.\s*)?' + [regex]::Escape($toggleHandler.FlagName)
				$showPattern = '(?i)(?:Видимость|Visible)\s*=\s*(?:Истина|True|' + $flagPattern + ')'
				$showMatch = [regex]::Match($toggleHandler.Body, $showPattern)
				if ($showMatch.Success -and $showMatch.Index -lt $firstServerCall) {
					Report-Warn "11. ${ctx}: DynamicList '$attrName' checkbox handler '$($toggleHandler.Handler)' makes the panel visible before the server-bound parameter initialization; initialize first, then show"
				}
				$reachableToggleText = Get-DlReachableBsl $routines15 $toggleHandler.Handler
				foreach ($table in $boundTables) {
					$tableName = $table.GetAttribute('name')
					$refreshPattern = '(?i)(?:Элементы|Items)\s*\.\s*' + [regex]::Escape($tableName) + '\s*\.\s*(?:Обновить|Refresh)\s*\('
					if ($reachableToggleText -match $refreshPattern) {
						Report-Warn "11. ${ctx}: DynamicList '$attrName' checkbox path explicitly refreshes table '$tableName' after/beside parameter initialization; changing DynamicList.Parameters already schedules reread, so verify and remove the extra refresh"
						break
					}
				}
			}
		}
		$initiallyHidden = $true; foreach ($table in $boundTables) { if (-not (Test-DlHidden $table $formRoot15)) { $initiallyHidden = $false; break } }

		$defaulted = @{}
		foreach ($parameter in $settings.SelectNodes("*[local-name()='Parameter']")) {
			$nameNode = $parameter.SelectSingleNode("*[local-name()='name']"); $valueNode = $parameter.SelectSingleNode("*[local-name()='value']")
			if (-not $nameNode -or -not $valueNode) { continue }
			$isNil = $valueNode.GetAttribute('nil', 'http://www.w3.org/2001/XMLSchema-instance').ToLowerInvariant() -eq 'true'
			if (-not $isNil) { $defaulted[$nameNode.InnerText.Trim().ToLowerInvariant()] = $true }
		}

		if ($initiallyHidden) {
			$missing = @($queryParams | Where-Object { -not $defaulted.ContainsKey($_.ToLowerInvariant()) -and -not (Test-DlSetter $allRoutineText15 $attrName $_) })
			if ($missing.Count -gt 0) { Report-Warn "11. ${ctx}: DynamicList '$attrName' is initially hidden, but no parameter setter was found for: $($missing -join ', '); initialize before making its table visible" }
			continue
		}

		$missing = @($queryParams | Where-Object { -not $defaulted.ContainsKey($_.ToLowerInvariant()) -and -not (Test-DlSetter $onCreateText15 $attrName $_) })
		if ($missing.Count -gt 0) {
			$tableNames = @($boundTables | ForEach-Object { $_.GetAttribute('name') }) -join ', '
			Report-Warn "11. ${ctx}: DynamicList '$attrName' is initially visible in table(s) $tableNames, but OnCreateAtServer does not initialize query parameter(s): $($missing -join ', '); initialize them there or keep the table/ancestor Visible=false until initialization"
		}
		$hasSetter = $false; foreach ($parameter in $queryParams) { if (Test-DlSetter $onCreateText15 $attrName $parameter) { $hasSetter = $true; break } }
		if ($onCreateText15 -and $hasSetter) {
			foreach ($table in $boundTables) {
				$tableName = $table.GetAttribute('name'); $refreshPattern = '(?i)(?:Элементы|Items)\s*\.\s*' + [regex]::Escape($tableName) + '\s*\.\s*(?:Обновить|Refresh)\s*\('
				if ($onCreateText15 -match $refreshPattern) { Report-Warn "11. ${ctx}: DynamicList '$attrName' explicitly refreshes table '$tableName' in OnCreateAtServer; it is redundant before first display"; break }
			}
		}
	}

	if ($formRawText -match '<BaseForm') {
		# Check BaseForm has version
		if ($formRawText -notmatch '<BaseForm[^>]+version=') {
			Report-Warn "11. ${ctx}: <BaseForm> missing version attribute"
		}

		$script:borrowedFormsWithTree += @{
			Path = $formXmlFile; RawText = $formRawText; Context = $ctx
		}
	}
}

if ($formCount -eq 0) {
	Report-OK "11. Borrowed forms: none found"
} elseif ($check11Ok) {
	$bfCount = $script:borrowedFormsWithTree.Count
	Report-OK "11. Borrowed forms: $formCount validated ($bfCount with BaseForm)"
}

if ($script:stopped) { & $finalize; exit 1 }

# --- Check 12: Form dependency references ---
$platformStyleItems = @{
	"TableHeaderBackColor"=$true; "AccentColor"=$true; "NormalTextFont"=$true
	"FormBackColor"=$true; "ToolTipBackColor"=$true; "BorderColor"=$true
	"FieldBackColor"=$true; "FieldTextColor"=$true; "ButtonBackColor"=$true
	"ButtonTextColor"=$true; "AlternateRowColor"=$true; "SpecialTextColor"=$true
	"TextFont"=$true; "ImportantColor"=$true; "FormTextColor"=$true
	"SmallTextFont"=$true; "ExtraLargeTextFont"=$true; "LargeTextFont"=$true
	"NormalTextColor"=$true; "GroupHeaderBackColor"=$true; "GroupHeaderFont"=$true
	"ErrorColor"=$true; "SuccessColor"=$true; "WarningColor"=$true
}
$check12Ok = $true
$depCheckCount = 0

foreach ($bf in $script:borrowedFormsWithTree) {
	$raw = $bf.RawText
	$ctx = $bf.Context
	$missingItems = @()

	# CommonPicture references
	$cpRefs = @{}
	foreach ($m in [regex]::Matches($raw, '<xr:Ref>CommonPicture\.(\w+)</xr:Ref>')) {
		$cpRefs[$m.Groups[1].Value] = $true
	}
	$cpIndex = $script:childObjectIndex["CommonPicture"]
	foreach ($cpName in $cpRefs.Keys) {
		$depCheckCount++
		if (-not $cpIndex -or -not $cpIndex.ContainsKey($cpName)) {
			$missingItems += "CommonPicture.${cpName}"
		}
	}

	# StyleItem references
	$siRefs = @{}
	foreach ($m in [regex]::Matches($raw, 'style:([A-Za-z\u0410-\u044F\u0401\u0451_][A-Za-z0-9\u0410-\u044F\u0401\u0451_]*)')) {
		$siRefs[$m.Groups[1].Value] = $true
	}
	$siIndex = $script:childObjectIndex["StyleItem"]
	foreach ($siName in $siRefs.Keys) {
		$depCheckCount++
		if ($platformStyleItems.ContainsKey($siName)) { continue }
		if (-not $siIndex -or -not $siIndex.ContainsKey($siName)) {
			$missingItems += "StyleItem.${siName}"
		}
	}

	# Enum DesignTimeRef references
	$enumRefs = @{}
	foreach ($m in [regex]::Matches($raw, 'xr:DesignTimeRef">Enum\.(\w+)\.EnumValue\.(\w+)')) {
		$eKey = "$($m.Groups[1].Value).$($m.Groups[2].Value)"
		$enumRefs[$eKey] = @{ Enum = $m.Groups[1].Value; Value = $m.Groups[2].Value }
	}
	$eIndex = $script:childObjectIndex["Enum"]
	foreach ($entry in $enumRefs.Values) {
		$depCheckCount++
		if (-not $eIndex -or -not $eIndex.ContainsKey($entry.Enum)) {
			$missingItems += "Enum.$($entry.Enum)"
		} elseif (-not $script:enumValuesIndex.ContainsKey($entry.Enum) -or -not $script:enumValuesIndex[$entry.Enum].ContainsKey($entry.Value)) {
			$missingItems += "Enum.$($entry.Enum).EnumValue.$($entry.Value)"
		}
	}

		# Корень путей формы — имя её основного реквизита: «Объект» только у формы объекта, у формы
	# списка «Список», у формы записи регистра «Запись». С зашитым «Объект» обе проверки ниже на
	# таких формах молча не срабатывали. Ищем сначала в <Attributes> самой формы, потом в <BaseForm>.
	$rootName = ""
	$rootMatch = [regex]::Match($raw, '(?s)<Attribute name="([^"]+)"[^>]*>(?:(?!</Attribute>).)*?<MainAttribute>true</MainAttribute>')
	if ($rootMatch.Success) { $rootName = $rootMatch.Groups[1].Value }

	# <AdditionalColumns table="Объект.X"> — доп. колонки табличной части, объявленные в самой форме.
	# Колонки есть, а самой ТЧ в расширении нет → платформа отвергает загрузку: «Неверный путь к
	# данным» плюс «Колонки не могут быть добавлены к реквизиту».
	$acTables = @{}
	if ($rootName) {
		$rootPat = [regex]::Escape($rootName)
		foreach ($m in [regex]::Matches($raw, "<AdditionalColumns table=`"${rootPat}\.(\w+)`"")) {
			$acTables[$m.Groups[1].Value] = $true
		}
	}
	# Соседние проверки этого блока эвристичны (имя стиля добывается регуляркой), поэтому там
	# предупреждение. Здесь сигнал точный — имя ТЧ берётся из атрибута, — а последствие жёсткое,
	# поэтому ошибка.
	if ($acTables.Count -gt 0) {
		$ownerKey = ($ctx -split '\.Form\.')[0]
		$ownerTS = $script:borrowedTSIndex[$ownerKey]
		foreach ($tblName in $acTables.Keys) {
			$depCheckCount++
			if (-not $ownerTS -or -not $ownerTS.ContainsKey($tblName)) {
				Report-Error "12. ${ctx}: <AdditionalColumns table=`"${rootName}.${tblName}`"> — TabularSection.${tblName} not borrowed in extension"
				$check12Ok = $false
			}
		}
	}

	foreach ($mi in $missingItems) {
		Report-Warn "12. ${ctx}: references ${mi} not borrowed in extension"
		$check12Ok = $false
	}
}

if ($script:borrowedFormsWithTree.Count -eq 0) {
	Report-OK "12. Form dependencies: no borrowed forms with tree"
} elseif ($check12Ok) {
	Report-OK "12. Form dependencies: $depCheckCount references checked"
}

if ($script:stopped) { & $finalize; exit 1 }

# --- Check 13: TypeLink with human-readable paths ---
$check13Ok = $true
$typeLinkCount = 0

foreach ($bf in $script:borrowedFormsWithTree) {
	$raw = $bf.RawText
	$ctx = $bf.Context
	$matches = [regex]::Matches($raw, '<TypeLink>\s*<xr:DataPath>Items\.[^<]*</xr:DataPath>')
	if ($matches.Count -gt 0) {
		$typeLinkCount += $matches.Count
		Report-Warn "13. ${ctx}: $($matches.Count) TypeLink(s) with human-readable Items.* DataPath (should be stripped)"
		$check13Ok = $false
	}
}

if ($script:borrowedFormsWithTree.Count -eq 0) {
	Report-OK "13. TypeLink: no borrowed forms with tree"
} elseif ($check13Ok) {
	Report-OK "13. TypeLink: clean"
}

# --- Check 14: пути Объект.* заимствованных форм против конфигурации-источника ---
# Требует -ConfigPath: отличить живой путь от висячего можно только по исходному объекту.
# «Объект.Партнер» валиден и без заимствования реквизита (наследуется от базы), а «Объект.Товары.Артикул»
# не разрешится нигде, если Артикул — не реквизит объекта и не колонка из <Columns> самой формы.
# Такой путь платформа отвергает на загрузке: «Неверный путь к данным».
if (-not $script:stopped -and $script:borrowedFormsWithTree.Count -gt 0) {
	if (-not $ConfigPath) {
		Out-Line "[INFO]  14. Пути Объект.* против конфигурации-источника не проверялись: не задан -ConfigPath"
	} else {
		$cfgRoot = $ConfigPath
		if (-not [System.IO.Path]::IsPathRooted($cfgRoot)) { $cfgRoot = Join-Path (Get-Location).Path $cfgRoot }
		if ((Test-Path $cfgRoot) -and -not (Test-Path $cfgRoot -PathType Container)) { $cfgRoot = Split-Path $cfgRoot -Parent }

		if (-not (Test-Path (Join-Path $cfgRoot "Configuration.xml"))) {
			Report-Warn "14. -ConfigPath '$ConfigPath': Configuration.xml не найден — проверка путей пропущена"
		} else {
			$check14Ok = $true
			$pathCheckCount = 0

			foreach ($bf in $script:borrowedFormsWithTree) {
				$raw = $bf.RawText
				$ctx = $bf.Context
				# Корень путей — имя основного реквизита формы (см. проверку 12). Нет его ни в
				# <Attributes> формы, ни в <BaseForm> — путей с корнем не бывает, проверять нечего.
				$rootMatch14 = [regex]::Match($raw, '(?s)<Attribute name="([^"]+)"[^>]*>(?:(?!</Attribute>).)*?<MainAttribute>true</MainAttribute>')
				if (-not $rootMatch14.Success) { continue }
				$rootName = $rootMatch14.Groups[1].Value
				# У динамического списка набор полей — результат его запроса, а не состав объекта:
				# туда входят и стандартные поля списка (Ref, Date, DefaultPicture), и псевдонимы
				# запроса. Сверять такие пути с ChildObjects объекта нельзя — будут ложные ошибки
				# (корпусная проверка: 3383 таких сегмента на 1094 формах списка УТ).
				if ($rootMatch14.Value -match '>cfg:DynamicList<') { continue }
				$ownerKey = ($ctx -split '\.Form\.')[0]
				$ownerParts = $ownerKey -split '\.', 2
				if ($ownerParts.Count -lt 2) { continue }
				$ownerType = $ownerParts[0]; $ownerName = $ownerParts[1]
				$ownerDir = $childTypeDirMap[$ownerType]
				if (-not $ownerDir) { continue }
				$srcObjFile = Join-Path (Join-Path $cfgRoot $ownerDir) "${ownerName}.xml"
				if (-not (Test-Path $srcObjFile)) {
					Report-Warn "14. ${ctx}: объект-источник не найден в конфигурации ($ownerDir/${ownerName}.xml)"
					continue
				}

				# Имена, доступные первым сегментом пути: реквизиты и ТЧ объекта-источника.
				# Плюс для каждой ТЧ — её колонки: второй сегмент проверяем по ним (именно там
				# и жил дефект — Объект.Товары.Артикул при живой ТЧ Товары).
				$srcNames = @{}
				$srcTSColumns = @{}
				$srcDoc = New-Object System.Xml.XmlDocument
				$srcDoc.PreserveWhitespace = $false
				$srcDoc.Load($srcObjFile)
				$srcObjEl = $null
				foreach ($c in $srcDoc.DocumentElement.ChildNodes) {
					if ($c.NodeType -eq 'Element') { $srcObjEl = $c; break }
				}
				$srcChildObjects = if ($srcObjEl) { $srcObjEl.SelectSingleNode("*[local-name()='ChildObjects']") } else { $null }
				if ($srcChildObjects) {
					foreach ($sub in $srcChildObjects.ChildNodes) {
						if ($sub.NodeType -ne 'Element') { continue }
						# У регистра дочерние объекты — Dimension/Resource, а не Attribute: без них замена
						# корня превратила бы тихий пропуск в ложные ошибки на форме записи.
						if ($sub.LocalName -notin @('Attribute','Dimension','Resource','TabularSection')) { continue }
						$nameNode = $sub.SelectSingleNode("*[local-name()='Properties']/*[local-name()='Name']")
						if (-not $nameNode) { continue }
						$subName = $nameNode.InnerText.Trim()
						$srcNames[$subName] = $true
						if ($sub.LocalName -ne 'TabularSection') { continue }
						$cols = @{}
						foreach ($colName in $sub.SelectNodes("*[local-name()='ChildObjects']/*[local-name()='Attribute']/*[local-name()='Properties']/*[local-name()='Name']")) {
							$cols[$colName.InnerText.Trim()] = $true
						}
						$srcTSColumns[$subName] = $cols
					}
				}
				# Плюс колонки, объявленные в самой форме через <Columns>/<AdditionalColumns table="Объект.X">
				$rootPat14 = [regex]::Escape($rootName)
				foreach ($acm in [regex]::Matches($raw, "(?s)<AdditionalColumns table=`"${rootPat14}\.(\w+)`">(.*?)</AdditionalColumns>")) {
					$tbl = $acm.Groups[1].Value
					if (-not $srcTSColumns.ContainsKey($tbl)) { $srcTSColumns[$tbl] = @{} }
					foreach ($cm in [regex]::Matches($acm.Groups[2].Value, '<Column name="(\w+)"')) {
						$srcTSColumns[$tbl][$cm.Groups[1].Value] = $true
					}
				}

				$badPaths = @{}
				foreach ($m in [regex]::Matches($raw, "<(?:\w+:)?\w*DataPath[^>]*>${rootPat14}\.([^<]+)</(?:\w+:)?\w*DataPath>")) {
					$segments = $m.Groups[1].Value -split '\.'
					$seg0 = $segments[0]
					$pathCheckCount++
					if ($script:standardObjectFields -contains $seg0) { continue }
					if (-not $srcNames.ContainsKey($seg0)) {
						$badPaths["${rootName}.${seg0}"] = "у ${ownerKey} нет такого реквизита или табличной части"
						continue
					}
					# Второй сегмент проверяем только для табличных частей: у ссылочного реквизита
					# он ведёт в чужой объект, и это уже другая проверка.
					if ($segments.Count -lt 2 -or -not $srcTSColumns.ContainsKey($seg0)) { continue }
					$seg1 = $segments[1]
					if ($script:standardObjectFields -contains $seg1) { continue }
					# Итог колонки — псевдополе платформы: Total<Колонка> при живой колонке законен
					if ($seg1 -like "Total*" -and $srcTSColumns[$seg0].ContainsKey($seg1.Substring(5))) { continue }
					if (-not $srcTSColumns[$seg0].ContainsKey($seg1)) {
						$badPaths["${rootName}.${seg0}.${seg1}"] = "у табличной части ${seg0} нет колонки ${seg1}, и <Columns> формы её не объявляет"
					}
				}
				foreach ($bad in ($badPaths.Keys | Sort-Object)) {
					Report-Error "14. ${ctx}: путь '${bad}' — $($badPaths[$bad])"
					$check14Ok = $false
				}
			}

			if ($check14Ok) {
				Report-OK "14. Object paths vs source config: $pathCheckCount checked"
			}
		}
	}
}

# --- Check 15: основные роли расширения не дают прав на заимствованные объекты ---
# Платформа: «Назначение прав доступа на заимствованные объекты основными ролями в
# расширениях недопустимо». Роль вне <DefaultRoles> так делать вправе — проверяем только
# основные. Ловится статически, а по симптому (отказ загрузки) причина не читается.
$defaultRoleNodes = @($cfgNode.SelectNodes("md:Properties/md:DefaultRoles/xr:Item", $ns))
if ($defaultRoleNodes.Count -gt 0) {
	$adoptedCache = @{}

	function Test-ObjectAdopted {
		param([string]$typeName, [string]$objName)
		$key = "$typeName.$objName"
		if ($adoptedCache.ContainsKey($key)) { return $adoptedCache[$key] }
		$adoptedCache[$key] = $false
		if ($childTypeDirMap.ContainsKey($typeName)) {
			$objPath = Join-Path (Join-Path $configDir $childTypeDirMap[$typeName]) "$objName.xml"
			if (Test-Path $objPath) {
				try {
					$objDoc = New-Object System.Xml.XmlDocument
					$objDoc.Load($objPath)
					$objNs = New-Object System.Xml.XmlNamespaceManager($objDoc.NameTable)
					$objNs.AddNamespace("md", "http://v8.1c.ru/8.3/MDClasses")
					$ob = $objDoc.SelectSingleNode("/md:MetaDataObject/md:$typeName/md:Properties/md:ObjectBelonging", $objNs)
					if ($ob -and $ob.InnerText -eq "Adopted") { $adoptedCache[$key] = $true }
				} catch {}
			}
		}
		return $adoptedCache[$key]
	}

	$check15Ok = $true
	$check15Count = 0
	foreach ($rn in $defaultRoleNodes) {
		$roleRef = $rn.InnerText
		if ($roleRef -notmatch '^Role\.(.+)$') { continue }
		$defRoleName = $Matches[1]
		$rightsPath = Join-Path (Join-Path (Join-Path $configDir "Roles") $defRoleName) "Ext\Rights.xml"
		if (-not (Test-Path $rightsPath)) { continue }
		try {
			$rDoc = New-Object System.Xml.XmlDocument
			$rDoc.Load($rightsPath)
		} catch {
			continue
		}
		$rNs = New-Object System.Xml.XmlNamespaceManager($rDoc.NameTable)
		$rNs.AddNamespace("r", "http://v8.1c.ru/8.2/roles")
		foreach ($nameNode in $rDoc.SelectNodes("/r:Rights/r:object/r:name", $rNs)) {
			$fullName = $nameNode.InnerText
			$segs = $fullName.Split(".")
			# Configuration.* — права самого расширения, не объект; заимствования там нет.
			if ($segs.Count -lt 2 -or $segs[0] -eq "Configuration") { continue }
			$check15Count++
			if (Test-ObjectAdopted $segs[0] $segs[1]) {
				Report-Error ("15. Роль '$defRoleName' входит в DefaultRoles и даёт права на заимствованный $($segs[0]).$($segs[1]) " +
					"($fullName): платформа это запрещает. Вынесите такие права в отдельную роль вне DefaultRoles.")
				$check15Ok = $false
			}
		}
	}
	if ($check15Ok -and $check15Count -gt 0) {
		Report-OK "15. Основные роли: прав на заимствованные объекты нет ($check15Count checked)"
	}
}

if ($script:stopped) { & $finalize; exit 1 }

# --- Check 16: модуль заимствованного объекта и пометка расширенного свойства ---
# Свойство <xr:PropertyState> появилось в формате 2.19 (8.3.26); ниже платформа его молча
# выбрасывает, поэтому там проверять нечего. С 2.19 состояние обязано соответствовать факту:
# есть файл модуля — есть пометка, и наоборот. Перекос платформа принимает (проверено на стенде),
# но выгрузка Конфигуратора так не выглядит — отсюда предупреждение, а не ошибка.
if ($versionRank -ge 219 -and $childObjNode) {
	$stateIssues = @()
	$stateChecked = 0
	foreach ($child in $childObjNode.ChildNodes) {
		if ($child.NodeType -ne 'Element') { continue }
		$typeName = $child.LocalName
		if (-not $moduleKindsByType.ContainsKey($typeName)) { continue }
		if (-not $childTypeDirMap.ContainsKey($typeName)) { continue }
		$stateObjName = $child.InnerText.Trim()
		if (-not $stateObjName) { continue }
		$typeDir = Join-Path $configDir $childTypeDirMap[$typeName]
		$objFile = Join-Path $typeDir "$stateObjName.xml"
		if (-not (Test-Path $objFile)) { continue }
		$objText = [System.IO.File]::ReadAllText($objFile, [System.Text.Encoding]::UTF8)
		if ($objText -notmatch '<ObjectBelonging>Adopted</ObjectBelonging>') { continue }

		foreach ($kind in $moduleKindsByType[$typeName]) {
			$stateChecked++
			$hasFile = Test-Path (Join-Path (Join-Path (Join-Path $typeDir $stateObjName) "Ext") "$kind.bsl")
			$hasFlag = $objText -match "<xr:Property>$kind</xr:Property>"
			if ($hasFile -and -not $hasFlag) {
				$stateIssues += "$typeName.$stateObjName — есть $kind.bsl, но нет <xr:PropertyState> для $kind"
			} elseif ($hasFlag -and -not $hasFile) {
				$stateIssues += "$typeName.$stateObjName — есть <xr:PropertyState> для $kind, но нет $kind.bsl"
			}
		}
	}

	if ($stateChecked -gt 0) {
		if ($stateIssues.Count -eq 0) {
			Report-OK "16. Модули заимствованных объектов: пометки расширенных свойств согласованы ($stateChecked)"
		} else {
			foreach ($issue in $stateIssues) { Report-Warn "16. $issue" }
		}
	}
}

# --- Breadcrumb: controlled methods (&ИзменениеИКонтроль) drift is not checked here ---
$extRootDir = Split-Path $resolvedPath -Parent
$ctrlCount = 0
foreach ($bslFile in (Get-ChildItem -Path $extRootDir -Recurse -Filter *.bsl -File -ErrorAction SilentlyContinue)) {
	$txt = [System.IO.File]::ReadAllText($bslFile.FullName, [System.Text.Encoding]::UTF8)
	$ctrlCount += ([regex]::Matches($txt, '(?m)^\s*&ИзменениеИКонтроль\(')).Count
	if ($txt -match '(?i)\b(?:Список|ДинамическийСписок)\w*\.ТекущаяСтрока\b') {
		$relativeBsl = $bslFile.FullName.Substring($extRootDir.Length).TrimStart('\','/')
		Report-Warn "17. ${relativeBsl}: suspicious DynamicList.ТекущаяСтрока; use form table element .ТекущиеДанные or verify the runtime type"
	}
}
if ($ctrlCount -gt 0) {
	Out-Line "[INFO]  Контролируемых методов (&ИзменениеИКонтроль): $ctrlCount — их актуальность здесь не проверяется. Сверьте: /cfe-patch-method -Check -ExtensionPath <ext> -ConfigPath <cf>"
}

# --- Final output ---
& $finalize

if ($script:errors -gt 0) {
	exit 1
}
exit 0
