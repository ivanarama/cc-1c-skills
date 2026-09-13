#!/usr/bin/env python3
# cfe-validate v1.16 — Validate 1C configuration extension XML structure (CFE)
# Source: https://github.com/Nikolay-Shirokov/cc-1c-skills
"""Validates extension Configuration.xml: root, InternalInfo, extension properties, ChildObjects, borrowed objects."""
import sys, os, argparse, re
from lxml import etree

# Регистронезависимый ввод — паритет с PS1: в PowerShell имена параметров и [ValidateSet]
# регистр не различают, в argparse совпадение точное.
def ci_parse_args(parser, argv=None):
    """parse_args по правилам PS: имена параметров и значения choices регистронезависимы."""
    argv = list(sys.argv[1:] if argv is None else argv)
    names = {s.lower(): s for a in parser._actions for s in a.option_strings}
    for i, tok in enumerate(argv):
        if tok.startswith('-') and tok.lower() in names:
            argv[i] = names[tok.lower()]
    # choices — зеркало [ValidateSet]; канонизируем ДО разбора, иначе argparse отвергнет регистр
    choice_map = {}
    for a in parser._actions:
        if a.choices:
            for s in a.option_strings:
                choice_map[s] = {str(c).lower(): c for c in a.choices}
    for i in range(len(argv) - 1):
        m = choice_map.get(argv[i])
        if m and argv[i + 1].lower() in m:
            argv[i + 1] = m[argv[i + 1].lower()]
    return parser.parse_args(argv)


NS = {
    'md':  'http://v8.1c.ru/8.3/MDClasses',
    'v8':  'http://v8.1c.ru/8.1/data/core',
    'xr':  'http://v8.1c.ru/8.3/xcf/readable',
    'xsi': 'http://www.w3.org/2001/XMLSchema-instance',
    'xs':  'http://www.w3.org/2001/XMLSchema',
    'app': 'http://v8.1c.ru/8.2/managed-application/core',
}
FORM_NS = 'http://v8.1c.ru/8.3/xcf/logform'
V8_NS = 'http://v8.1c.ru/8.1/data/core'
FORM_NSMAP = {'f': FORM_NS, 'v8': V8_NS}

GUID_PATTERN = re.compile(
    r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
)
IDENT_PATTERN = re.compile(
    r'^[A-Za-z\u0410-\u042F\u0401\u0430-\u044F\u0451_]'
    r'[A-Za-z0-9\u0410-\u042F\u0401\u0430-\u044F\u0451_]*$'
)

# 7 fixed ClassIds for Configuration
VALID_CLASS_IDS = [
    '9cd510cd-abfc-11d4-9434-004095e12fc7',
    '9fcd25a0-4822-11d4-9414-008048da11f9',
    'e3687481-0a87-462c-a166-9f34594f9bba',
    '9de14907-ec23-4a07-96f0-85521cb6b53b',
    '51f2d5d8-ea4d-4064-8892-82951750031e',
    'e68182ea-4237-4383-967f-90c1e3370bc7',
    'fb282519-d103-4dd3-bc12-cb271d631dfc',
]

# 46 types in canonical order
CHILD_OBJECT_TYPES = [
    'Language', 'Subsystem', 'StyleItem', 'Style',
    'CommonPicture', 'SessionParameter', 'Role', 'CommonTemplate',
    'FilterCriterion', 'CommonModule', 'CommonAttribute', 'ExchangePlan',
    'XDTOPackage', 'WebService', 'HTTPService', 'WSReference',
    'EventSubscription', 'ScheduledJob', 'SettingsStorage', 'FunctionalOption',
    'FunctionalOptionsParameter', 'DefinedType', 'Bot', 'PaletteColor', 'CommonCommand', 'CommandGroup',
    'Constant', 'CommonForm', 'Catalog', 'Document',
    'DocumentNumerator', 'Sequence', 'DocumentJournal', 'Enum',
    'Report', 'DataProcessor', 'InformationRegister', 'AccumulationRegister',
    'ChartOfCharacteristicTypes', 'ChartOfAccounts', 'AccountingRegister',
    'ChartOfCalculationTypes', 'CalculationRegister',
    'BusinessProcess', 'Task', 'ExternalDataSource', 'IntegrationService',
]

# Модули заимствованных объектов: тип → виды модулей. Имя свойства в <xr:PropertyState>
# совпадает с базовым именем файла модуля. Копия таблицы есть в cfe-borrow (навыки автономны).
MODULE_KINDS_BY_TYPE = {
    "CommonModule": ["Module"], "HTTPService": ["Module"], "WebService": ["Module"],
    "Catalog": ["ObjectModule", "ManagerModule"], "Document": ["ObjectModule", "ManagerModule"],
    "Report": ["ObjectModule", "ManagerModule"], "DataProcessor": ["ObjectModule", "ManagerModule"],
    "ExchangePlan": ["ObjectModule", "ManagerModule"],
    "ChartOfCharacteristicTypes": ["ObjectModule", "ManagerModule"],
    "ChartOfAccounts": ["ObjectModule", "ManagerModule"],
    "ChartOfCalculationTypes": ["ObjectModule", "ManagerModule"],
    "BusinessProcess": ["ObjectModule", "ManagerModule"], "Task": ["ObjectModule", "ManagerModule"],
    "InformationRegister": ["RecordSetModule", "ManagerModule"],
    "AccumulationRegister": ["RecordSetModule", "ManagerModule"],
    "AccountingRegister": ["RecordSetModule", "ManagerModule"],
    "CalculationRegister": ["RecordSetModule", "ManagerModule"],
    "Sequence": ["RecordSetModule", "ManagerModule"],
    "Constant": ["ValueManagerModule", "ManagerModule"],
    "Enum": ["ManagerModule"], "DocumentJournal": ["ManagerModule"],
    "FilterCriterion": ["ManagerModule"],
}

# Type -> directory mapping
CHILD_TYPE_DIR_MAP = {
    'Language': 'Languages', 'Subsystem': 'Subsystems', 'StyleItem': 'StyleItems', 'Style': 'Styles',
    'CommonPicture': 'CommonPictures', 'SessionParameter': 'SessionParameters', 'Role': 'Roles',
    'CommonTemplate': 'CommonTemplates', 'FilterCriterion': 'FilterCriteria', 'CommonModule': 'CommonModules',
    'Bot': 'Bots', 'PaletteColor': 'PaletteColors',
    'CommonAttribute': 'CommonAttributes', 'ExchangePlan': 'ExchangePlans', 'XDTOPackage': 'XDTOPackages',
    'WebService': 'WebServices', 'HTTPService': 'HTTPServices', 'WSReference': 'WSReferences',
    'EventSubscription': 'EventSubscriptions', 'ScheduledJob': 'ScheduledJobs',
    'SettingsStorage': 'SettingsStorages', 'FunctionalOption': 'FunctionalOptions',
    'FunctionalOptionsParameter': 'FunctionalOptionsParameters', 'DefinedType': 'DefinedTypes',
    'CommonCommand': 'CommonCommands', 'CommandGroup': 'CommandGroups', 'Constant': 'Constants',
    'CommonForm': 'CommonForms', 'Catalog': 'Catalogs', 'Document': 'Documents',
    'DocumentNumerator': 'DocumentNumerators', 'Sequence': 'Sequences',
    'DocumentJournal': 'DocumentJournals', 'Enum': 'Enums', 'Report': 'Reports',
    'DataProcessor': 'DataProcessors', 'InformationRegister': 'InformationRegisters',
    'AccumulationRegister': 'AccumulationRegisters',
    'ChartOfCharacteristicTypes': 'ChartsOfCharacteristicTypes',
    'ChartOfAccounts': 'ChartsOfAccounts', 'AccountingRegister': 'AccountingRegisters',
    'ChartOfCalculationTypes': 'ChartsOfCalculationTypes',
    'CalculationRegister': 'CalculationRegisters',
    'BusinessProcess': 'BusinessProcesses', 'Task': 'Tasks',
    'ExternalDataSource': 'ExternalDataSources',
    'IntegrationService': 'IntegrationServices',
}

# Наборы GeneratedType по типу объекта (эталон — таблица §2.5 спецификации конфигурации).
# Неполный набор в заимствованной оболочке платформа отвергает при загрузке: «отсутствует один
# или более типов объекта <Тип>». Типы, у которых GeneratedType нет вовсе (общие модули,
# подписки, регламентные задания и т.п.), в карте отсутствуют — для них проверка не выполняется.
GENERATED_TYPE_CATEGORIES = {
    'Catalog':                    ['Object', 'Ref', 'Selection', 'List', 'Manager'],
    'Document':                   ['Object', 'Ref', 'Selection', 'List', 'Manager'],
    'Enum':                       ['Ref', 'Manager', 'List'],
    'Constant':                   ['Manager', 'ValueManager', 'ValueKey'],
    'Report':                     ['Object', 'Manager'],
    'DataProcessor':              ['Object', 'Manager'],
    'ExchangePlan':               ['Object', 'Ref', 'Selection', 'List', 'Manager'],
    'Task':                       ['Object', 'Ref', 'Selection', 'List', 'Manager'],
    'BusinessProcess':            ['Object', 'Ref', 'Selection', 'List', 'Manager', 'RoutePointRef'],
    'ChartOfCharacteristicTypes': ['Object', 'Ref', 'Selection', 'List', 'Manager', 'Characteristic'],
    'ChartOfAccounts':            ['Object', 'Ref', 'Selection', 'List', 'Manager', 'ExtDimensionTypes', 'ExtDimensionTypesRow'],
    'ChartOfCalculationTypes':    ['Object', 'Ref', 'Selection', 'List', 'Manager', 'DisplacingCalculationTypes', 'DisplacingCalculationTypesRow', 'BaseCalculationTypes', 'BaseCalculationTypesRow', 'LeadingCalculationTypes', 'LeadingCalculationTypesRow'],
    'InformationRegister':        ['Record', 'Manager', 'Selection', 'List', 'RecordSet', 'RecordKey', 'RecordManager'],
    'AccumulationRegister':       ['Record', 'Manager', 'Selection', 'List', 'RecordSet', 'RecordKey'],
    'AccountingRegister':         ['Record', 'Manager', 'Selection', 'List', 'RecordSet', 'RecordKey', 'ExtDimensions'],
    'CalculationRegister':        ['Record', 'Manager', 'Selection', 'List', 'RecordSet', 'RecordKey', 'Recalcs'],
    'DocumentJournal':            ['Selection', 'List', 'Manager'],
    'Sequence':                   ['Record', 'Manager', 'RecordSet'],
    'FilterCriterion':            ['Manager', 'List'],
    'SettingsStorage':            ['Manager'],
    'ExternalDataSource':         ['Manager', 'TablesManager', 'CubesManager'],
    'IntegrationService':         ['Manager'],
    'WSReference':                ['Manager'],
    'DefinedType':                ['DefinedType'],
}

# Стандартные реквизиты объектов: в ChildObjects их нет, но пути Объект.<Стандартный> законны.
# Имена зависят от варианта встроенного языка, поэтому держим оба написания.
# Основной реквизит формы: <Attribute name="X"> с <MainAttribute>true</MainAttribute> внутри
MAIN_ATTR_RE = re.compile(
    r'<Attribute name=\"([^\"]+)\"[^>]*>(?:(?!</Attribute>).)*?<MainAttribute>true</MainAttribute>', re.DOTALL)

STANDARD_OBJECT_FIELDS = {
    'Code', 'Description', 'Ref', 'Parent', 'Owner', 'DeletionMark', 'Predefined', 'IsFolder', 'LineNumber',
    'Number', 'Date', 'Posted', 'PredefinedDataName', 'RegisterRecords', 'DataVersion', 'RowsCount',
    'Код', 'Наименование', 'Ссылка', 'Родитель', 'Владелец', 'ПометкаУдаления', 'Предопределенный',
    'ЭтоГруппа', 'НомерСтроки', 'Номер', 'Дата', 'Проведен', 'ИмяПредопределенныхДанных',
    'Движения', 'ВерсияДанных', 'КоличествоСтрок',
}

# Valid enum values for extension properties
VALID_ENUM_VALUES = {
    'ConfigurationExtensionCompatibilityMode': [
        'DontUse', 'Version8_1', 'Version8_2_13', 'Version8_2_16',
        'Version8_3_1', 'Version8_3_2', 'Version8_3_3', 'Version8_3_4', 'Version8_3_5',
        'Version8_3_6', 'Version8_3_7', 'Version8_3_8', 'Version8_3_9', 'Version8_3_10',
        'Version8_3_11', 'Version8_3_12', 'Version8_3_13', 'Version8_3_14', 'Version8_3_15',
        'Version8_3_16', 'Version8_3_17', 'Version8_3_18', 'Version8_3_19', 'Version8_3_20',
        'Version8_3_21', 'Version8_3_22', 'Version8_3_23', 'Version8_3_24', 'Version8_3_25',
        'Version8_3_26', 'Version8_3_27', 'Version8_3_28', 'Version8_5_1',
    ],
    'DefaultRunMode': ['ManagedApplication', 'OrdinaryApplication', 'Auto'],
    'ScriptVariant': ['Russian', 'English'],
    'InterfaceCompatibilityMode': [
        'Version8_2', 'Version8_2EnableTaxi', 'Taxi', 'TaxiEnableVersion8_2',
        'TaxiEnableVersion8_5', 'Version8_5EnableTaxi', 'Version8_5',
    ],
}

EXPECTED_NS = 'http://v8.1c.ru/8.3/MDClasses'

# ── Format version ───────────────────────────────────────────
# Проверенный диапазон версий формата выгрузки: 2.17 (8.3.24) … 2.21 (8.5). Полная лестница —
# docs/1c-configuration-spec.md, «Лестница версий». Версию задаёт платформа ВЫГРУЗКИ, а не режим
# совместимости конфигурации. Версии ниже 2.17 (платформы 8.3.23 и старше) существуют, но навыки
# на них не проверялись — это предупреждение о непокрытии, а не о некорректности файла.
FORMAT_VERIFIED_MIN = "2.17"
FORMAT_VERIFIED_MAX = "2.21"


def format_rank(ver):
    """"2.20" → 220, "2.9" → 209. Строковое сравнение неверно ("2.9" > "2.17")."""
    m = re.match(r'^(\d+)\.(\d+)$', ver or '')
    return int(m.group(1)) * 100 + int(m.group(2)) if m else 0


_DL_IDENT = r'[A-Za-z\u0410-\u042F\u0401\u0430-\u044F\u0451_][A-Za-z0-9\u0410-\u042F\u0401\u0430-\u044F\u0451_]*'


def _dl_text(node, name):
    child = node.find(f'{{{FORM_NS}}}{name}')
    return (child.text or '').strip() if child is not None else ''


def _dl_query_parameters(query):
    result, seen = [], set()
    for match in re.finditer(r'&(' + _DL_IDENT + r')', query or ''):
        name = match.group(1)
        if name.casefold() not in seen:
            seen.add(name.casefold())
            result.append(name)
    return result


def _dl_split_items(text):
    result, current, depth, in_string = [], [], 0, False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '"':
            current.append(ch)
            if in_string and i + 1 < len(text) and text[i + 1] == '"':
                current.append(text[i + 1]); i += 2; continue
            in_string = not in_string
        elif not in_string and ch == '(':
            depth += 1; current.append(ch)
        elif not in_string and ch == ')':
            depth = max(0, depth - 1); current.append(ch)
        elif not in_string and depth == 0 and ch == ',':
            item = ''.join(current).strip()
            if item:
                result.append(item)
            current = []
        else:
            current.append(ch)
        i += 1
    item = ''.join(current).strip()
    if item:
        result.append(item)
    return result


def _dl_aggregate_key_info(query):
    aggregate = r'(?:\u0421\u0423\u041c\u041c\u0410|\u041a\u041e\u041b\u0418\u0427\u0415\u0421\u0422\u0412\u041e|\u0421\u0420\u0415\u0414\u041d\u0415\u0415|\u041c\u0418\u041d\u0418\u041c\u0423\u041c|\u041c\u0410\u041a\u0421\u0418\u041c\u0423\u041c|SUM|COUNT|AVG|MIN|MAX)'
    if not re.search(r'(?i)\b' + aggregate + r'\s*\(', query or ''):
        return set(), set()
    if re.search(r'(?i)\b(?:\u041e\u0411\u042a\u0415\u0414\u0418\u041d\u0418\u0422\u042c|UNION)\b', query):
        return set(), set()
    select_match = re.search(r'(?is)\b(?:\u0412\u042b\u0411\u0420\u0410\u0422\u042c|SELECT)\b(.*?)\b(?:\u0418\u0417|FROM)\b', query)
    group_match = re.search(
        r'(?is)\b(?:\u0421\u0413\u0420\u0423\u041f\u041f\u0418\u0420\u041e\u0412\u0410\u0422\u042c\s+\u041f\u041e|GROUP\s+BY)\b(.*?)'
        r'(?=\b(?:\u0418\u041c\u0415\u042e\u0429\u0418\u0415|HAVING|\u0423\u041f\u041e\u0420\u042f\u0414\u041e\u0427\u0418\u0422\u042c\s+\u041f\u041e|ORDER\s+BY|\u0418\u0422\u041e\u0413\u0418|TOTALS)\b|$)', query)
    if not select_match or not group_match:
        return set(), set()
    norm = lambda value: re.sub(r'\s+', '', value or '').casefold()
    aliases, aggregates = {}, set()
    alias_re = re.compile(r'(?is)^(.*?)\s+(?:\u041a\u0410\u041a|AS)\s+(' + _DL_IDENT + r')\s*$')
    for item in _dl_split_items(select_match.group(1)):
        match = alias_re.match(item)
        if match:
            expr, alias = match.group(1), match.group(2)
        else:
            simple = re.search(r'(' + _DL_IDENT + r')\s*$', item)
            if not simple:
                continue
            expr, alias = item, simple.group(1)
        aliases[norm(expr)] = alias
        if re.search(r'(?i)\b' + aggregate + r'\s*\(', expr):
            aggregates.add(alias.casefold())
    grouped = set()
    for expr in _dl_split_items(group_match.group(1)):
        alias = aliases.get(norm(expr))
        if alias:
            grouped.add(alias.casefold())
        elif re.fullmatch(_DL_IDENT, expr.strip()):
            grouped.add(expr.strip().casefold())
        else:
            return set(), aggregates
    return grouped, aggregates


def _dl_strip_bsl_comments(text):
    result = []
    for line in (text or '').splitlines():
        out, in_string, i = [], False, 0
        while i < len(line):
            ch = line[i]
            if ch == '"':
                out.append(ch)
                if in_string and i + 1 < len(line) and line[i + 1] == '"':
                    out.append(line[i + 1]); i += 2; continue
                in_string = not in_string
            elif not in_string and ch == '/' and i + 1 < len(line) and line[i + 1] == '/':
                break
            else:
                out.append(ch)
            i += 1
        result.append(''.join(out))
    return '\n'.join(result)


def _dl_bsl_routines(text):
    pattern = re.compile(
        r'(?ims)^\s*(?:\u041f\u0440\u043e\u0446\u0435\u0434\u0443\u0440\u0430|Procedure|\u0424\u0443\u043d\u043a\u0446\u0438\u044f|Function)\s+(' + _DL_IDENT + r')\s*\([^)]*\)(.*?)'
        r'^\s*(?:\u041a\u043e\u043d\u0435\u0446\u041f\u0440\u043e\u0446\u0435\u0434\u0443\u0440\u044b|EndProcedure|\u041a\u043e\u043d\u0435\u0446\u0424\u0443\u043d\u043a\u0446\u0438\u0438|EndFunction)\b')
    return {
        match.group(1).casefold(): (match.group(1), match.group(2))
        for match in pattern.finditer(_dl_strip_bsl_comments(text))
    }


def _dl_reachable_bsl(routines, handler):
    queue, seen, bodies = [handler.casefold()], set(), []
    while queue:
        name = queue.pop(0)
        if name in seen or name not in routines:
            continue
        seen.add(name)
        body = routines[name][1]
        bodies.append(body)
        for called_key, (called_name, _called_body) in routines.items():
            if called_key not in seen and re.search(r'(?i)(?<![A-Za-z0-9_\u0410-\u044f\u0401\u0451])' + re.escape(called_name) + r'\s*\(', body):
                queue.append(called_key)
    return '\n'.join(bodies)


def _dl_server_routine_names(text):
    clean = _dl_strip_bsl_comments(text)
    pattern = re.compile(
        r'(?im)^\s*&\s*(?:\u041d\u0430\u0421\u0435\u0440\u0432\u0435\u0440\u0435(?:\u0411\u0435\u0437\u041a\u043e\u043d\u0442\u0435\u043a\u0441\u0442\u0430)?|AtServer(?:NoContext)?)\s*$\s*'
        r'^\s*(?:\u041f\u0440\u043e\u0446\u0435\u0434\u0443\u0440\u0430|Procedure|\u0424\u0443\u043d\u043a\u0446\u0438\u044f|Function)\s+(' + _DL_IDENT + r')\s*\('
    )
    return {match.group(1).casefold() for match in pattern.finditer(clean)}


def _dl_routine_reaches_server(routines, name, server_names, memo, visiting=None):
    key = name.casefold()
    if key in memo:
        return memo[key]
    if key in server_names:
        memo[key] = True
        return True
    if key not in routines:
        memo[key] = False
        return False
    visiting = set() if visiting is None else set(visiting)
    if key in visiting:
        return False
    visiting.add(key)
    body = routines[key][1]
    for called_key, (called_name, _called_body) in routines.items():
        if re.search(r'(?i)(?<![A-Za-z0-9_\u0410-\u044f\u0401\u0451])' + re.escape(called_name) + r'\s*\(', body):
            if _dl_routine_reaches_server(routines, called_key, server_names, memo, visiting):
                memo[key] = True
                return True
    memo[key] = False
    return False


def _dl_first_server_call_position(routines, handler, server_names):
    routine = routines.get(handler.casefold())
    if routine is None:
        return None
    body, memo, positions = routine[1], {}, []
    for called_key, (called_name, _called_body) in routines.items():
        if not _dl_routine_reaches_server(routines, called_key, server_names, memo):
            continue
        call_re = re.compile(r'(?i)(?<![A-Za-z0-9_\u0410-\u044f\u0401\u0451])' + re.escape(called_name) + r'\s*\(')
        positions.extend(match.start() for match in call_re.finditer(body))
    return min(positions) if positions else None


def _dl_has_disabled_early_return(body, flag_name, first_server_call):
    flag = r'(?:\u042d\u0442\u0430\u0424\u043e\u0440\u043c\u0430\s*\.\s*|ThisForm\s*\.\s*)?' + re.escape(flag_name)
    condition = r'(?:(?:\u041d\u0435|Not)\s+' + flag + r'|' + flag + r'\s*=\s*(?:\u041b\u043e\u0436\u044c|False))'
    guard_re = re.compile(
        r'(?is)\b(?:\u0415\u0441\u043b\u0438|If)\s+' + condition
        + r'\s+(?:\u0422\u043e\u0433\u0434\u0430|Then)\b(.*?)\b(?:\u041a\u043e\u043d\u0435\u0446\u0415\u0441\u043b\u0438|EndIf)\b'
    )
    for match in guard_re.finditer(body or ''):
        if match.start() >= first_server_call:
            continue
        has_return = re.search(r'(?i)\b(?:\u0412\u043e\u0437\u0432\u0440\u0430\u0442|Return)\s*;', match.group(1))
        if has_return and match.end() <= first_server_call:
            return True
    return False


def _dl_toggle_handlers(root, routines, bound_tables):
    controlled_names = set()
    for table in bound_tables:
        node = table
        while node is not None and node is not root:
            if isinstance(node.tag, str) and node.get('name'):
                controlled_names.add(node.get('name').casefold())
            node = node.getparent()
    result = []
    for checkbox in root.xpath('./f:ChildItems//f:CheckBoxField', namespaces=FORM_NSMAP):
        flag_name = _dl_text(checkbox, 'DataPath')
        if not flag_name:
            continue
        for event in checkbox.xpath('./f:Events/f:Event', namespaces=FORM_NSMAP):
            if (event.get('name') or '').casefold() not in ('onchange', '\u043f\u0440\u0438\u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u0438'):
                continue
            handler = (event.text or '').strip()
            routine = routines.get(handler.casefold())
            if not routine:
                continue
            body = routine[1]
            for element_name in controlled_names:
                pattern = (
                    r'(?i)(?:\u042d\u043b\u0435\u043c\u0435\u043d\u0442\u044b|Items)\s*\.\s*' + re.escape(element_name)
                    + r'\s*\.\s*(?:\u0412\u0438\u0434\u0438\u043c\u043e\u0441\u0442\u044c|Visible)\s*='
                )
                if re.search(pattern, body):
                    result.append((flag_name, handler, body))
                    break
    return result


def _dl_hidden(node, root):
    while node is not None and node is not root:
        visible = node.find(f'{{{FORM_NS}}}Visible')
        if visible is not None and (visible.text or '').strip().casefold() == 'false':
            return True
        node = node.getparent()
    return False


def _dl_setter_found(text, attr_name, parameter):
    return bool(re.search(
        r'(?i)(?<![A-Za-z0-9_\u0410-\u044f\u0401\u0451])(?:\u042d\u0442\u0430\u0424\u043e\u0440\u043c\u0430\s*\.\s*|ThisForm\s*\.\s*)?'
        + re.escape(attr_name) + r'\s*\.\s*(?:\u041f\u0430\u0440\u0430\u043c\u0435\u0442\u0440\u044b|Parameters)\s*\.\s*'
        + r'(?:\u0423\u0441\u0442\u0430\u043d\u043e\u0432\u0438\u0442\u044c\u0417\u043d\u0430\u0447\u0435\u043d\u0438\u0435\u041f\u0430\u0440\u0430\u043c\u0435\u0442\u0440\u0430|SetParameterValue)\s*\(\s*"'
        + re.escape(parameter) + r'"', text or ''))


class Reporter:
    def __init__(self, max_errors, detailed=False):
        self.errors = 0
        self.warnings = 0
        self.ok_count = 0
        self.stopped = False
        self.max_errors = max_errors
        self.detailed = detailed
        self.lines = []
        self.obj_name = '(unknown)'

    def out(self, msg=''):
        self.lines.append(msg)

    def ok(self, msg):
        self.ok_count += 1
        if self.detailed:
            self.lines.append(f'[OK]    {msg}')

    def error(self, msg):
        self.errors += 1
        self.lines.append(f'[ERROR] {msg}')
        if self.errors >= self.max_errors:
            self.stopped = True

    def warn(self, msg):
        self.warnings += 1
        self.lines.append(f'[WARN]  {msg}')

    def text(self):
        return '\r\n'.join(self.lines) + '\r\n'

    def finalize(self, out_file):
        checks = self.ok_count + self.errors + self.warnings
        if self.errors == 0 and self.warnings == 0 and not self.detailed:
            result = f'=== Validation OK: Extension.{self.obj_name} ({checks} checks) ==='
        else:
            self.out('')
            self.out(f'=== Result: {self.errors} errors, {self.warnings} warnings ({checks} checks) ===')
            result = self.text()

        print(result, end='' if '\r\n' in result else '\n')

        if out_file:
            with open(out_file, 'w', encoding='utf-8-sig', newline='') as f:
                f.write(result)
            print(f'Written to: {out_file}')


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description='Validate 1C configuration extension XML structure (CFE)', allow_abbrev=False
    )
    parser.add_argument('-ExtensionPath', '-Path', dest='ExtensionPath', required=True)
    parser.add_argument('-Detailed', action='store_true')
    parser.add_argument('-MaxErrors', dest='MaxErrors', type=int, default=30)
    parser.add_argument('-OutFile', dest='OutFile', default='')
    # Конфигурация-источник. Без неё проверки, требующие сравнения с основной конфигурацией,
    # пропускаются (о чём сказано в отчёте), остальные работают как раньше.
    parser.add_argument('-ConfigPath', dest='ConfigPath', default='')
    args = ci_parse_args(parser)

    extension_path = args.ExtensionPath
    max_errors = args.MaxErrors
    out_file = args.OutFile
    config_path_arg = args.ConfigPath

    # --- Resolve path ---
    if not os.path.isabs(extension_path):
        extension_path = os.path.join(os.getcwd(), extension_path)

    if os.path.isdir(extension_path):
        candidate = os.path.join(extension_path, 'Configuration.xml')
        if os.path.exists(candidate):
            extension_path = candidate
        else:
            print(f'[ERROR] No Configuration.xml found in directory: {extension_path}')
            sys.exit(1)

    if not os.path.exists(extension_path):
        print(f'[ERROR] File not found: {extension_path}')
        sys.exit(1)

    resolved_path = os.path.abspath(extension_path)
    config_dir = os.path.dirname(resolved_path)

    if out_file and not os.path.isabs(out_file):
        out_file = os.path.join(os.getcwd(), out_file)

    r = Reporter(max_errors, detailed=args.Detailed)
    r.out('')

    # --- 1. Parse XML ---
    xml_doc = None
    try:
        xml_parser = etree.XMLParser(remove_blank_text=False)
        xml_doc = etree.parse(resolved_path, xml_parser)
    except etree.XMLSyntaxError as e:
        r.lines.insert(0, '=== Validation: Extension (parse failed) ===')
        r.out('')
        r.error(f'1. XML parse failed: {e}')
        r.finalize(out_file)
        sys.exit(1)

    root = xml_doc.getroot()

    # --- Check 1: Root structure ---
    check1_ok = True
    root_local = etree.QName(root.tag).localname
    root_ns = etree.QName(root.tag).namespace or ''

    if root_local != 'MetaDataObject':
        r.error(f"1. Root element is '{root_local}', expected 'MetaDataObject'")
        r.finalize(out_file)
        sys.exit(1)

    if root_ns != EXPECTED_NS:
        r.error(f"1. Root namespace is '{root_ns}', expected '{EXPECTED_NS}'")
        check1_ok = False

    version = root.get('version', '')
    version_rank = format_rank(version)
    if not version:
        r.warn('1. Missing version attribute on MetaDataObject')
    elif version_rank == 0:
        r.error(f"1. Malformed version '{version}' (expected N.N)")
    elif version_rank < format_rank(FORMAT_VERIFIED_MIN):
        r.warn(f"1. Format version '{version}' is below the tested range "
               f"{FORMAT_VERIFIED_MIN}-{FORMAT_VERIFIED_MAX} — skills were not verified on it")
    elif version_rank > format_rank(FORMAT_VERIFIED_MAX):
        r.warn(f"1. Format version '{version}' is above the tested range "
               f"{FORMAT_VERIFIED_MIN}-{FORMAT_VERIFIED_MAX} — skills were not verified on it")

    # Must have Configuration child
    cfg_node = None
    for child in root:
        if not isinstance(child.tag, str):
            continue
        if etree.QName(child.tag).localname == 'Configuration' and etree.QName(child.tag).namespace == EXPECTED_NS:
            cfg_node = child
            break

    if cfg_node is None:
        r.error('1. No <Configuration> element found inside MetaDataObject')
        r.finalize(out_file)
        sys.exit(1)

    # UUID
    cfg_uuid = cfg_node.get('uuid', '')
    if not cfg_uuid:
        r.error('1. Missing uuid on <Configuration>')
        check1_ok = False
    elif not GUID_PATTERN.match(cfg_uuid):
        r.error(f"1. Invalid uuid '{cfg_uuid}' on <Configuration>")
        check1_ok = False

    # Get name early for header
    props_node = cfg_node.find('md:Properties', NS)
    name_node = props_node.find('md:Name', NS) if props_node is not None else None
    obj_name = (name_node.text or '') if name_node is not None and name_node.text else '(unknown)'
    r.obj_name = obj_name

    r.lines.insert(0, f'=== Validation: Extension.{obj_name} ===')

    if check1_ok:
        r.ok(f'1. Root structure: MetaDataObject/Configuration, version {version}')

    if r.stopped:
        r.finalize(out_file)
        sys.exit(1)

    # --- Check 2: InternalInfo ---
    internal_info = cfg_node.find('md:InternalInfo', NS)
    check2_ok = True

    if internal_info is None:
        r.error('2. InternalInfo: missing')
    else:
        contained = internal_info.findall('xr:ContainedObject', NS)
        if len(contained) != 7:
            r.warn(f'2. InternalInfo: expected 7 ContainedObject, found {len(contained)}')

        found_class_ids = {}
        for co in contained:
            class_id_el = co.find('xr:ClassId', NS)
            object_id_el = co.find('xr:ObjectId', NS)

            if class_id_el is None or not (class_id_el.text or ''):
                r.error('2. ContainedObject missing ClassId')
                check2_ok = False
                continue

            cid = class_id_el.text
            if cid not in VALID_CLASS_IDS:
                r.error(f'2. Unknown ClassId: {cid}')
                check2_ok = False

            if cid in found_class_ids:
                r.error(f'2. Duplicate ClassId: {cid}')
                check2_ok = False
            found_class_ids[cid] = True

            if object_id_el is None or not (object_id_el.text or ''):
                r.error(f'2. ContainedObject missing ObjectId for ClassId {cid}')
                check2_ok = False
            elif not GUID_PATTERN.match(object_id_el.text):
                r.error(f"2. Invalid ObjectId '{object_id_el.text}' for ClassId {cid}")
                check2_ok = False

        missing_ids = [cid for cid in VALID_CLASS_IDS if cid not in found_class_ids]
        if len(missing_ids) > 0:
            r.warn(f'2. Missing ClassIds: {len(missing_ids)} of 7')

        if check2_ok:
            r.ok(f'2. InternalInfo: {len(contained)} ContainedObject, all ClassIds valid')

    if r.stopped:
        r.finalize(out_file)
        sys.exit(1)

    # --- Check 3: Extension-specific properties ---
    def_lang = ''

    if props_node is None:
        r.error('3. Properties block missing')
    else:
        check3_ok = True

        # ObjectBelonging = Adopted
        ob_node = props_node.find('md:ObjectBelonging', NS)
        ob_val = (ob_node.text or '') if ob_node is not None else ''
        if ob_val != 'Adopted':
            r.error(f"3. ObjectBelonging must be 'Adopted', got '{ob_val}'")
            check3_ok = False

        # Name
        if name_node is None or not (name_node.text or ''):
            r.error('3. Name is missing or empty')
            check3_ok = False
        else:
            name_val = name_node.text
            if not IDENT_PATTERN.match(name_val):
                r.error(f"3. Name '{name_val}' is not a valid 1C identifier")
                check3_ok = False

        # ConfigurationExtensionPurpose
        purpose_node = props_node.find('md:ConfigurationExtensionPurpose', NS)
        valid_purposes = ['Patch', 'Customization', 'AddOn']
        if purpose_node is None or not (purpose_node.text or ''):
            r.error('3. ConfigurationExtensionPurpose is missing')
            check3_ok = False
        elif purpose_node.text not in valid_purposes:
            r.error(f"3. ConfigurationExtensionPurpose '{purpose_node.text}' invalid (expected: Patch, Customization, AddOn)")
            check3_ok = False

        # NamePrefix
        prefix_node = props_node.find('md:NamePrefix', NS)
        if prefix_node is None or not (prefix_node.text or ''):
            r.warn('3. NamePrefix is empty')

        # KeepMappingToExtendedConfigurationObjectsByIDs
        keep_map_node = props_node.find('md:KeepMappingToExtendedConfigurationObjectsByIDs', NS)
        if keep_map_node is None:
            r.warn('3. KeepMappingToExtendedConfigurationObjectsByIDs is missing')

        # DefaultLanguage
        def_lang_node = props_node.find('md:DefaultLanguage', NS)
        def_lang = (def_lang_node.text or '') if def_lang_node is not None else ''

        if check3_ok:
            purpose_val = purpose_node.text if purpose_node is not None and purpose_node.text else '?'
            prefix_val = (prefix_node.text or '') if prefix_node is not None and prefix_node.text else '(empty)'
            r.ok(f'3. Extension properties: Name="{obj_name}", Purpose={purpose_val}, Prefix={prefix_val}')

    if r.stopped:
        r.finalize(out_file)
        sys.exit(1)

    # --- Check 4: Enum property values ---
    if props_node is not None:
        enum_checked = 0
        check4_ok = True

        for prop_name, allowed in VALID_ENUM_VALUES.items():
            prop_node = props_node.find(f'md:{prop_name}', NS)
            if prop_node is not None and prop_node.text:
                val = prop_node.text
                if val not in allowed:
                    r.error(f"4. Property '{prop_name}' has invalid value '{val}'")
                    check4_ok = False
                enum_checked += 1

        if check4_ok:
            r.ok(f'4. Property values: {enum_checked} enum properties checked')
    else:
        r.warn('4. No Properties block to check')

    if r.stopped:
        r.finalize(out_file)
        sys.exit(1)

    # --- Check 5: ChildObjects -- valid types, no duplicates, order ---
    child_obj_node = cfg_node.find('md:ChildObjects', NS)

    if child_obj_node is None:
        r.error('5. ChildObjects block missing')
    else:
        check5_ok = True
        total_count = 0
        child_object_index = {}
        duplicates = {}
        type_first_index = {}
        last_type_order = -1
        order_ok = True

        for child in child_obj_node:
            if not isinstance(child.tag, str):
                continue
            type_name = etree.QName(child.tag).localname
            obj_name_val = child.text or ''

            if type_name in CHILD_OBJECT_TYPES:
                type_idx = CHILD_OBJECT_TYPES.index(type_name)
            else:
                type_idx = -1

            if type_idx < 0:
                r.error(f"5. Unknown type '{type_name}' in ChildObjects")
                check5_ok = False
            else:
                if type_name not in type_first_index:
                    type_first_index[type_name] = type_idx
                    if type_idx < last_type_order:
                        r.warn(f"5. Type '{type_name}' is out of canonical order (after type at position {last_type_order})")
                        order_ok = False
                    last_type_order = type_idx

            if type_name not in child_object_index:
                child_object_index[type_name] = {}
            if obj_name_val in child_object_index[type_name]:
                dup_key = f'{type_name}.{obj_name_val}'
                if dup_key not in duplicates:
                    r.error(f'5. Duplicate: {dup_key}')
                    duplicates[dup_key] = True
                    check5_ok = False
            else:
                child_object_index[type_name][obj_name_val] = True

            total_count += 1

        type_count = len(child_object_index)
        if check5_ok:
            order_info = ', order correct' if order_ok else ''
            r.ok(f'5. ChildObjects: {type_count} types, {total_count} objects{order_info}')

    if r.stopped:
        r.finalize(out_file)
        sys.exit(1)

    # --- Check 6: DefaultLanguage references existing Language in ChildObjects ---
    if def_lang and child_obj_node is not None:
        lang_name = def_lang
        if lang_name.startswith('Language.'):
            lang_name = lang_name[9:]

        found = False
        for child in child_obj_node:
            if not isinstance(child.tag, str):
                continue
            if etree.QName(child.tag).localname == 'Language' and (child.text or '') == lang_name:
                found = True
                break

        if found:
            r.ok(f'6. DefaultLanguage "{def_lang}" found in ChildObjects')
        else:
            r.error(f'6. DefaultLanguage "{def_lang}" not found in ChildObjects')
    else:
        if not def_lang:
            r.warn('6. Cannot check DefaultLanguage (empty)')
        else:
            r.warn('6. Cannot check DefaultLanguage (no ChildObjects)')

    if r.stopped:
        r.finalize(out_file)
        sys.exit(1)

    # --- Check 7: Language files exist ---
    if child_obj_node is not None:
        lang_names = []
        for child in child_obj_node:
            if not isinstance(child.tag, str):
                continue
            if etree.QName(child.tag).localname == 'Language':
                lang_names.append(child.text or '')

        if len(lang_names) > 0:
            exist_count = 0
            for ln in lang_names:
                lang_file = os.path.join(config_dir, 'Languages', ln + '.xml')
                if os.path.exists(lang_file):
                    exist_count += 1
                else:
                    r.warn(f'7. Language file missing: Languages/{ln}.xml')
            if exist_count == len(lang_names):
                r.ok(f'7. Language files: {exist_count}/{len(lang_names)} exist')
        else:
            r.warn('7. No Language entries in ChildObjects')
    else:
        r.warn('7. Cannot check language files (no ChildObjects)')

    if r.stopped:
        r.finalize(out_file)
        sys.exit(1)

    # --- Check 8: Object directories exist ---
    if child_obj_node is not None:
        dirs_to_check = {}
        for child in child_obj_node:
            if not isinstance(child.tag, str):
                continue
            type_name = etree.QName(child.tag).localname
            if type_name == 'Language':
                continue
            if type_name in CHILD_TYPE_DIR_MAP:
                dir_name = CHILD_TYPE_DIR_MAP[type_name]
                dirs_to_check[dir_name] = dirs_to_check.get(dir_name, 0) + 1

        missing_dirs = []
        for dir_name, count in dirs_to_check.items():
            dir_path = os.path.join(config_dir, dir_name)
            if not os.path.isdir(dir_path):
                missing_dirs.append(f'{dir_name} ({count} objects)')

        if len(missing_dirs) == 0:
            r.ok(f'8. Object directories: {len(dirs_to_check)} directories, all exist')
        else:
            for md in missing_dirs:
                r.warn(f'8. Missing directory: {md}')
    else:
        pass  # no ChildObjects

    if r.stopped:
        r.finalize(out_file)
        sys.exit(1)

    # --- Check 9: Borrowed objects + Check 10: Sub-items ---
    MD = NS['md']
    XR = NS['xr']
    enum_values_index = {}
    borrowed_ts_index = {}
    form_list = []

    def is_borrowed_sub_item(sub_item):
        """Check if sub-item has explicit borrowed metadata (ObjectBelonging or ExtendedConfigurationObject)."""
        sub_props = sub_item.find(f'{{{MD}}}Properties')
        if sub_props is None:
            return False
        sub_ob = sub_props.find(f'{{{MD}}}ObjectBelonging')
        if sub_ob is not None and (sub_ob.text or ''):
            return True
        sub_ext = sub_props.find(f'{{{MD}}}ExtendedConfigurationObject')
        return sub_ext is not None and bool(sub_ext.text or '')

    def validate_borrowed_sub_item(check_num, context, sub_type, sub_item):
        """Validate a borrowed Attribute/EnumValue/TabularSection sub-item."""
        sub_props = sub_item.find(f'{{{MD}}}Properties')
        if sub_props is None:
            r.error(f'{check_num}. {context}: {sub_type} missing Properties')
            return False
        ok = True
        sub_ob = sub_props.find(f'{{{MD}}}ObjectBelonging')
        if sub_ob is None or (sub_ob.text or '') != 'Adopted':
            r.error(f"{check_num}. {context}: {sub_type} ObjectBelonging must be 'Adopted'")
            ok = False
        sub_name = sub_props.find(f'{{{MD}}}Name')
        if sub_name is None or not (sub_name.text or ''):
            r.error(f'{check_num}. {context}: {sub_type} missing Name')
            ok = False
        sub_ext = sub_props.find(f'{{{MD}}}ExtendedConfigurationObject')
        sub_name_val = (sub_name.text or '') if sub_name is not None else '?'
        if sub_ext is None or not (sub_ext.text or ''):
            r.error(f'{check_num}. {context}: {sub_type}.{sub_name_val} missing ExtendedConfigurationObject')
            ok = False
        elif not GUID_PATTERN.match(sub_ext.text):
            r.error(f'{check_num}. {context}: {sub_type}.{sub_name_val} invalid ExtendedConfigurationObject')
            ok = False
        return ok

    if child_obj_node is not None:
        borrowed_count = 0
        borrowed_ok_count = 0
        check9_ok = True
        check10_ok = True
        sub_item_count = 0

        for child in child_obj_node:
            if not isinstance(child.tag, str):
                continue
            type_name = etree.QName(child.tag).localname
            child_name = child.text or ''
            if type_name == 'Language':
                continue

            if type_name not in CHILD_TYPE_DIR_MAP:
                continue
            dir_name = CHILD_TYPE_DIR_MAP[type_name]
            obj_file = os.path.join(config_dir, dir_name, child_name + '.xml')

            if not os.path.exists(obj_file):
                continue

            # Parse object XML
            try:
                obj_parser = etree.XMLParser(remove_blank_text=False)
                obj_doc = etree.parse(obj_file, obj_parser)
            except etree.XMLSyntaxError as e:
                r.warn(f'9. Cannot parse {dir_name}/{child_name}.xml: {e}')
                continue

            obj_root = obj_doc.getroot()

            # Find the object element (Catalog, Document, etc.)
            obj_el = None
            for c in obj_root:
                if isinstance(c.tag, str):
                    obj_el = c
                    break
            if obj_el is None:
                continue

            obj_props = obj_el.find(f'{{{MD}}}Properties')
            if obj_props is None:
                continue

            # --- Check 9: ObjectBelonging + ExtendedConfigurationObject ---
            ob_node = obj_props.find(f'{{{MD}}}ObjectBelonging')
            if ob_node is not None and (ob_node.text or '') == 'Adopted':
                borrowed_count += 1

                ext_obj = obj_props.find(f'{{{MD}}}ExtendedConfigurationObject')
                if ext_obj is None or not (ext_obj.text or ''):
                    r.error(f'9. Borrowed {type_name}.{child_name}: missing ExtendedConfigurationObject')
                    check9_ok = False
                elif not GUID_PATTERN.match(ext_obj.text):
                    r.error(f"9. Borrowed {type_name}.{child_name}: invalid ExtendedConfigurationObject UUID '{ext_obj.text}'")
                    check9_ok = False
                else:
                    borrowed_ok_count += 1

                # Полнота набора GeneratedType: платформа отвергает оболочку с неполным набором
                # («отсутствует один или более типов объекта ChartOfCharacteristicTypes»)
                expected_cats = GENERATED_TYPE_CATEGORIES.get(type_name)
                if expected_cats:
                    obj_info = obj_el.find(f'{{{MD}}}InternalInfo')
                    found_cats = set()
                    if obj_info is not None:
                        for gt in obj_info.findall(f'{{{XR}}}GeneratedType'):
                            cat = gt.get('category')
                            if cat:
                                found_cats.add(cat)
                    missing_cats = [c for c in expected_cats if c not in found_cats]
                    if missing_cats:
                        word = 'category' if len(missing_cats) == 1 else 'categories'
                        r.error(f"9. Borrowed {type_name}.{child_name}: missing GeneratedType {word} {', '.join(missing_cats)}")
                        check9_ok = False

            # --- Check 10: Sub-items (Attribute, TabularSection, EnumValue, Form) ---
            obj_child_objects = obj_el.find(f'{{{MD}}}ChildObjects')
            if obj_child_objects is not None:
                ctx = f'{type_name}.{child_name}'
                for sub_item in obj_child_objects:
                    if not isinstance(sub_item.tag, str):
                        continue
                    sub_type = etree.QName(sub_item.tag).localname

                    if sub_type == 'Attribute':
                        if not is_borrowed_sub_item(sub_item):
                            continue
                        sub_item_count += 1
                        if not validate_borrowed_sub_item('10', ctx, 'Attribute', sub_item):
                            check10_ok = False

                    elif sub_type == 'TabularSection':
                        if not is_borrowed_sub_item(sub_item):
                            continue
                        sub_item_count += 1
                        if not validate_borrowed_sub_item('10', ctx, 'TabularSection', sub_item):
                            check10_ok = False
                        else:
                            # Check InternalInfo GeneratedTypes
                            ts_info = sub_item.find(f'{{{MD}}}InternalInfo')
                            ts_name_el = sub_item.find(f'{{{MD}}}Properties/{{{MD}}}Name')
                            ts_label = (ts_name_el.text or '?') if ts_name_el is not None else '?'
                            # Индекс заимствованных ТЧ — по нему Check 12 сверяет <AdditionalColumns table="Объект.X">
                            if ts_name_el is not None and ts_name_el.text:
                                borrowed_ts_index.setdefault(f'{type_name}.{child_name}', {})[ts_name_el.text.strip()] = True
                            if ts_info is None:
                                r.error(f'10. {ctx}: TabularSection.{ts_label} missing InternalInfo')
                                check10_ok = False
                            else:
                                gt_nodes = ts_info.findall(f'{{{XR}}}GeneratedType')
                                has_ts = any(gt.get('category') == 'TabularSection' for gt in gt_nodes)
                                has_tsr = any(gt.get('category') == 'TabularSectionRow' for gt in gt_nodes)
                                if not has_ts or not has_tsr:
                                    r.error(f'10. {ctx}: TabularSection.{ts_label} missing GeneratedType (need TabularSection + TabularSectionRow)')
                                    check10_ok = False
                            # Recurse into TS ChildObjects/Attribute
                            ts_child_objs = sub_item.find(f'{{{MD}}}ChildObjects')
                            if ts_child_objs is not None:
                                for ts_attr in ts_child_objs:
                                    if not isinstance(ts_attr.tag, str):
                                        continue
                                    if etree.QName(ts_attr.tag).localname != 'Attribute':
                                        continue
                                    if not is_borrowed_sub_item(ts_attr):
                                        continue
                                    sub_item_count += 1
                                    if not validate_borrowed_sub_item('10', f'{ctx}.ТЧ.{ts_label}', 'Attribute', ts_attr):
                                        check10_ok = False

                    elif sub_type == 'EnumValue' and type_name == 'Enum':
                        if not is_borrowed_sub_item(sub_item):
                            continue
                        sub_item_count += 1
                        if validate_borrowed_sub_item('10', ctx, 'EnumValue', sub_item):
                            ev_name = sub_item.find(f'{{{MD}}}Properties/{{{MD}}}Name')
                            if ev_name is not None and (ev_name.text or ''):
                                if child_name not in enum_values_index:
                                    enum_values_index[child_name] = {}
                                enum_values_index[child_name][ev_name.text] = True
                        else:
                            check10_ok = False

                    elif sub_type == 'Form':
                        form_name = sub_item.text or ''
                        if form_name:
                            form_meta_file = os.path.join(config_dir, dir_name, child_name, 'Forms', form_name + '.xml')
                            if not os.path.exists(form_meta_file):
                                r.error(f'10. {ctx}: Form.{form_name} metadata file missing')
                                check10_ok = False
                            form_list.append({
                                'TypeName': type_name, 'ObjName': child_name,
                                'FormName': form_name, 'DirName': dir_name,
                            })
                            sub_item_count += 1

            if r.stopped:
                break

        if borrowed_count == 0:
            r.ok('9. Borrowed objects: none found')
        elif check9_ok:
            r.ok(f'9. Borrowed objects: {borrowed_ok_count}/{borrowed_count} validated')

        if sub_item_count == 0:
            r.ok('10. Sub-items: none found')
        elif check10_ok:
            r.ok(f'10. Sub-items: {sub_item_count} validated (Attributes, TabularSections, EnumValues, Forms)')

    if r.stopped:
        r.finalize(out_file)
        sys.exit(1)

    # --- Check 11: Borrowed form structure ---
    borrowed_forms_with_tree = []
    check11_ok = True
    form_count = 0

    for fi in form_list:
        form_count += 1
        form_base = os.path.join(config_dir, fi['DirName'], fi['ObjName'], 'Forms', fi['FormName'])
        form_meta_file = os.path.join(os.path.dirname(form_base), fi['FormName'] + '.xml')
        form_xml_file = os.path.join(form_base, 'Ext', 'Form.xml')
        module_bsl_file = os.path.join(form_base, 'Ext', 'Form', 'Module.bsl')
        ctx = f"{fi['TypeName']}.{fi['ObjName']}.Form.{fi['FormName']}"

        # Validate form metadata XML
        if os.path.exists(form_meta_file):
            try:
                fm_doc = etree.parse(form_meta_file, etree.XMLParser(remove_blank_text=False))
                fm_root = fm_doc.getroot()
                fm_el = None
                for c in fm_root:
                    if isinstance(c.tag, str):
                        fm_el = c
                        break
                if fm_el is not None:
                    fm_props = fm_el.find(f'{{{MD}}}Properties')
                    if fm_props is not None:
                        fm_ob = fm_props.find(f'{{{MD}}}ObjectBelonging')
                        is_borrowed = fm_ob is not None and (fm_ob.text or '') == 'Adopted'
                        if is_borrowed:
                            fm_ext = fm_props.find(f'{{{MD}}}ExtendedConfigurationObject')
                            if fm_ext is None or not (fm_ext.text or '') or not GUID_PATTERN.match(fm_ext.text or ''):
                                r.error(f'11. {ctx}: invalid/missing ExtendedConfigurationObject')
                                check11_ok = False
                        fm_type = fm_props.find(f'{{{MD}}}FormType')
                        if fm_type is not None and (fm_type.text or '') != 'Managed':
                            r.error(f"11. {ctx}: FormType must be 'Managed', got '{fm_type.text}'")
                            check11_ok = False
            except etree.XMLSyntaxError as e:
                r.warn(f'11. {ctx}: Cannot parse metadata: {e}')

        # Form.xml must exist
        if not os.path.exists(form_xml_file):
            r.error(f'11. {ctx}: Ext/Form.xml missing')
            check11_ok = False
            continue

        # Module.bsl should exist
        if not os.path.exists(module_bsl_file):
            r.warn(f'11. {ctx}: Ext/Form/Module.bsl missing')

        # Read Form.xml as raw text for BaseForm checks
        with open(form_xml_file, 'r', encoding='utf-8-sig') as f:
            form_raw_text = f.read()

        # Parse only the root form layer.  BaseForm is a borrowed snapshot; validating its dynamic
        # lists as if they were extension additions produces duplicate and misleading findings.
        try:
            form_root = etree.fromstring(form_raw_text.encode('utf-8'))
        except etree.XMLSyntaxError as e:
            r.error(f'11. {ctx}: Ext/Form.xml parse failed: {e}')
            check11_ok = False
            continue

        base_form = form_root.find(f'{{{FORM_NS}}}BaseForm')
        base_attr_names = {
            (node.get('name') or '').casefold()
            for node in (base_form.findall(f'{{{FORM_NS}}}Attributes/{{{FORM_NS}}}Attribute') if base_form is not None else [])
            if node.get('name')
        }
        module_text = ''
        if os.path.isfile(module_bsl_file):
            with open(module_bsl_file, 'r', encoding='utf-8-sig') as module_file:
                module_text = module_file.read()
        routines = _dl_bsl_routines(module_text)
        server_routine_names = _dl_server_routine_names(module_text)
        all_routine_text = '\n'.join(body for _name, body in routines.values())
        checked_toggle_handlers = set()
        on_create_handlers = [
            (node.text or '').strip()
            for node in form_root.xpath('./f:Events/f:Event', namespaces=FORM_NSMAP)
            if (node.get('name') or '').casefold() in ('oncreateatserver', '\u043f\u0440\u0438\u0441\u043e\u0437\u0434\u0430\u043d\u0438\u0438\u043d\u0430\u0441\u0435\u0440\u0432\u0435\u0440\u0435') and (node.text or '').strip()
        ]
        on_create_text = '\n'.join(_dl_reachable_bsl(routines, handler) for handler in on_create_handlers)

        for attr in form_root.xpath('./f:Attributes/f:Attribute', namespaces=FORM_NSMAP):
            attr_name = attr.get('name') or ''
            type_values = [str(value).strip() for value in attr.xpath('./f:Type/v8:Type/text()', namespaces=FORM_NSMAP)]
            if 'cfg:DynamicList' not in type_values:
                continue
            settings = attr.find(f'{{{FORM_NS}}}Settings')
            if settings is None or _dl_text(settings, 'ManualQuery').casefold() != 'true':
                continue
            query = _dl_text(settings, 'QueryText')
            if re.search(r'^\s*\|', query, re.M):
                r.error(f"11. {ctx}: DynamicList '{attr_name}' QueryText contains BSL string-literal '|' prefixes")
                check11_ok = False

            main_table = _dl_text(settings, 'MainTable')
            key_type = _dl_text(settings, 'KeyType')
            key_fields = [
                (node.text or '').strip() for node in settings.findall(f'{{{FORM_NS}}}KeyField')
                if (node.text or '').strip()
            ]
            key_folded = [value.casefold() for value in key_fields]
            if not main_table:
                if not key_type:
                    r.error(f"11. {ctx}: DynamicList '{attr_name}' without MainTable needs KeyType")
                    check11_ok = False
                elif key_type not in ('RowKey', 'FieldValue', 'RowNumber', 'Auto'):
                    r.error(f"11. {ctx}: DynamicList '{attr_name}' has unsupported KeyType '{key_type}'")
                    check11_ok = False
                elif key_type in ('RowKey', 'FieldValue') and not key_fields:
                    r.error(f"11. {ctx}: DynamicList '{attr_name}' KeyType={key_type} needs KeyField")
                    check11_ok = False
                elif key_type == 'FieldValue' and len(key_fields) != 1:
                    r.error(f"11. {ctx}: DynamicList '{attr_name}' KeyType=FieldValue needs exactly one KeyField")
                    check11_ok = False
                elif key_type == 'RowNumber' and key_fields:
                    r.warn(f"11. {ctx}: DynamicList '{attr_name}' KeyField is ignored for KeyType=RowNumber")

                duplicate_keys = sorted({value for value in key_folded if key_folded.count(value) > 1})
                if duplicate_keys:
                    r.error(f"11. {ctx}: DynamicList '{attr_name}' has duplicate KeyField(s): {', '.join(duplicate_keys)}")
                    check11_ok = False
                explicit_fields = {
                    (value or '').strip().casefold()
                    for value in settings.xpath('./f:Field/*[local-name()="dataPath"]/text()', namespaces=FORM_NSMAP)
                    if (value or '').strip()
                }
                unknown_keys = [value for value in key_fields if explicit_fields and value.casefold() not in explicit_fields]
                if unknown_keys:
                    r.error(f"11. {ctx}: DynamicList '{attr_name}' KeyField(s) absent from declared fields: {', '.join(unknown_keys)}")
                    check11_ok = False

                grouped, aggregates = _dl_aggregate_key_info(query)
                key_set = set(key_folded)
                missing_group_keys = sorted(grouped - key_set)
                if missing_group_keys:
                    r.warn(f"11. {ctx}: DynamicList '{attr_name}' aggregate key omits GROUP BY field(s) "
                           f"{', '.join(missing_group_keys)}; row uniqueness is not guaranteed")
                unstable_keys = sorted(aggregates & key_set)
                if unstable_keys:
                    r.warn(f"11. {ctx}: DynamicList '{attr_name}' uses aggregate result field(s) as KeyField "
                           f"({', '.join(unstable_keys)}); the row key changes with the aggregate")

            if re.search(r'^AccumulationRegister\.[^.]+\.Balance$', main_table):
                r.warn(f'11. {ctx}: virtual accumulation-register table is used as MainTable; '
                       'prefer query + RowKey/KeyField and no MainTable for an added list')

            query_params = _dl_query_parameters(query)
            is_added = base_form is not None and attr_name.casefold() not in base_attr_names
            if not is_added or not query_params:
                continue
            bound_tables = [
                table for table in form_root.xpath('./f:ChildItems//f:Table', namespaces=FORM_NSMAP)
                if _dl_text(table, 'DataPath').casefold() == attr_name.casefold()
            ]
            if not bound_tables:
                continue
            for flag_name, handler, handler_body in _dl_toggle_handlers(form_root, routines, bound_tables):
                handler_key = handler.casefold()
                if handler_key in checked_toggle_handlers:
                    continue
                checked_toggle_handlers.add(handler_key)
                first_server_call = _dl_first_server_call_position(routines, handler, server_routine_names)
                if first_server_call is not None and not _dl_has_disabled_early_return(
                    handler_body, flag_name, first_server_call
                ):
                    r.warn(
                        f"11. {ctx}: DynamicList '{attr_name}' checkbox handler '{handler}' can reach a local "
                        f"server routine before a recognized disabled-state guard for '{flag_name}'; "
                        "use `If Not <flag> Then ... Return; EndIf` before the server-bound call"
                    )
                if first_server_call is not None:
                    show_re = re.compile(
                        r'(?i)(?:\u0412\u0438\u0434\u0438\u043c\u043e\u0441\u0442\u044c|Visible)\s*=\s*'
                        r'(?:\u0418\u0441\u0442\u0438\u043d\u0430|True|(?:\u042d\u0442\u0430\u0424\u043e\u0440\u043c\u0430\s*\.\s*|ThisForm\s*\.\s*)?'
                        + re.escape(flag_name) + r')'
                    )
                    show_match = show_re.search(handler_body)
                    if show_match and show_match.start() < first_server_call:
                        r.warn(
                            f"11. {ctx}: DynamicList '{attr_name}' checkbox handler '{handler}' makes the "
                            "panel visible before the server-bound parameter initialization; initialize first, then show"
                        )
                    reachable_toggle_text = _dl_reachable_bsl(routines, handler)
                    for table in bound_tables:
                        refresh_re = re.compile(
                            r'(?i)(?:\u042d\u043b\u0435\u043c\u0435\u043d\u0442\u044b|Items)\s*\.\s*'
                            + re.escape(table.get('name', ''))
                            + r'\s*\.\s*(?:\u041e\u0431\u043d\u043e\u0432\u0438\u0442\u044c|Refresh)\s*\('
                        )
                        if refresh_re.search(reachable_toggle_text):
                            r.warn(
                                f"11. {ctx}: DynamicList '{attr_name}' checkbox path explicitly refreshes table "
                                f"'{table.get('name', '')}' after/beside parameter initialization; changing "
                                "DynamicList.Parameters already schedules reread, so verify and remove the extra refresh"
                            )
                            break
            initially_hidden = all(_dl_hidden(table, form_root) for table in bound_tables)

            defaulted = set()
            for parameter in settings.findall(f'{{{FORM_NS}}}Parameter'):
                names = parameter.xpath('./*[local-name()="name"]/text()')
                values = parameter.xpath('./*[local-name()="value"]')
                if names and values and (values[0].get('{http://www.w3.org/2001/XMLSchema-instance}nil') or '').casefold() != 'true':
                    defaulted.add((names[0] or '').strip().casefold())

            if initially_hidden:
                missing = [
                    value for value in query_params
                    if value.casefold() not in defaulted and not _dl_setter_found(all_routine_text, attr_name, value)
                ]
                if missing:
                    r.warn(f"11. {ctx}: DynamicList '{attr_name}' is initially hidden, but no parameter setter "
                           f"was found for: {', '.join(missing)}; initialize before making its table visible")
                continue

            missing = [
                value for value in query_params
                if value.casefold() not in defaulted and not _dl_setter_found(on_create_text, attr_name, value)
            ]
            if missing:
                table_names = ', '.join(table.get('name', '?') for table in bound_tables)
                r.warn(f"11. {ctx}: DynamicList '{attr_name}' is initially visible in table(s) {table_names}, "
                       f"but OnCreateAtServer does not initialize query parameter(s): {', '.join(missing)}; "
                       'initialize them there or keep the table/ancestor Visible=false until initialization')

            if on_create_text and any(_dl_setter_found(on_create_text, attr_name, value) for value in query_params):
                for table in bound_tables:
                    refresh = re.compile(r'(?i)(?:\u042d\u043b\u0435\u043c\u0435\u043d\u0442\u044b|Items)\s*\.\s*' + re.escape(table.get('name', ''))
                                         + r'\s*\.\s*(?:\u041e\u0431\u043d\u043e\u0432\u0438\u0442\u044c|Refresh)\s*\(')
                    if refresh.search(on_create_text):
                        r.warn(f"11. {ctx}: DynamicList '{attr_name}' explicitly refreshes table "
                               f"'{table.get('name', '')}' in OnCreateAtServer; it is redundant before first display")
                        break

        if '<BaseForm' in form_raw_text:
            if not re.search(r'<BaseForm[^>]+version=', form_raw_text):
                r.warn(f'11. {ctx}: <BaseForm> missing version attribute')
            borrowed_forms_with_tree.append({
                'Path': form_xml_file, 'RawText': form_raw_text, 'Context': ctx,
            })

    if form_count == 0:
        r.ok('11. Borrowed forms: none found')
    elif check11_ok:
        bf_count = len(borrowed_forms_with_tree)
        r.ok(f'11. Borrowed forms: {form_count} validated ({bf_count} with BaseForm)')

    if r.stopped:
        r.finalize(out_file)
        sys.exit(1)

    # --- Check 12: Form dependency references ---
    PLATFORM_STYLE_ITEMS = {
        'TableHeaderBackColor', 'AccentColor', 'NormalTextFont',
        'FormBackColor', 'ToolTipBackColor', 'BorderColor',
        'FieldBackColor', 'FieldTextColor', 'ButtonBackColor',
        'ButtonTextColor', 'AlternateRowColor', 'SpecialTextColor',
        'TextFont', 'ImportantColor', 'FormTextColor',
        'SmallTextFont', 'ExtraLargeTextFont', 'LargeTextFont',
        'NormalTextColor', 'GroupHeaderBackColor', 'GroupHeaderFont',
        'ErrorColor', 'SuccessColor', 'WarningColor',
    }
    check12_ok = True
    dep_check_count = 0

    for bf in borrowed_forms_with_tree:
        raw = bf['RawText']
        ctx = bf['Context']
        missing_items = []

        # CommonPicture references
        cp_refs = {}
        for m in re.finditer(r'<xr:Ref>CommonPicture\.(\w+)</xr:Ref>', raw):
            cp_refs[m.group(1)] = True
        cp_index = child_object_index.get('CommonPicture', {})
        for cp_name in cp_refs:
            dep_check_count += 1
            if cp_name not in cp_index:
                missing_items.append(f'CommonPicture.{cp_name}')

        # StyleItem references
        si_refs = {}
        for m in re.finditer(r'style:([A-Za-z\u0410-\u044F\u0401\u0451_][A-Za-z0-9\u0410-\u044F\u0401\u0451_]*)', raw):
            si_refs[m.group(1)] = True
        si_index = child_object_index.get('StyleItem', {})
        for si_name in si_refs:
            dep_check_count += 1
            if si_name in PLATFORM_STYLE_ITEMS:
                continue
            if si_name not in si_index:
                missing_items.append(f'StyleItem.{si_name}')

        # Enum DesignTimeRef references
        enum_refs = {}
        for m in re.finditer(r'xr:DesignTimeRef">Enum\.(\w+)\.EnumValue\.(\w+)', raw):
            e_key = f'{m.group(1)}.{m.group(2)}'
            enum_refs[e_key] = {'Enum': m.group(1), 'Value': m.group(2)}
        e_index = child_object_index.get('Enum', {})
        for entry in enum_refs.values():
            dep_check_count += 1
            if entry['Enum'] not in e_index:
                missing_items.append(f"Enum.{entry['Enum']}")
            elif entry['Enum'] not in enum_values_index or entry['Value'] not in enum_values_index.get(entry['Enum'], {}):
                missing_items.append(f"Enum.{entry['Enum']}.EnumValue.{entry['Value']}")

        # <AdditionalColumns table="Объект.X"> — доп. колонки табличной части, объявленные в самой форме.
        # Колонки есть, а самой ТЧ в расширении нет → платформа отвергает загрузку: «Неверный путь к
        # данным» плюс «Колонки не могут быть добавлены к реквизиту».
        # Соседние проверки этого блока эвристичны (имя стиля добывается регуляркой), поэтому там
        # предупреждение. Здесь сигнал точный — имя ТЧ берётся из атрибута, — а последствие жёсткое,
        # поэтому ошибка.
        # Корень путей формы — имя её основного реквизита: «Объект» только у формы объекта, у формы
        # списка «Список», у формы записи регистра «Запись». С зашитым «Объект» обе проверки на
        # таких формах молча не срабатывали. Ищем сначала в <Attributes> формы, потом в <BaseForm>.
        root_match = MAIN_ATTR_RE.search(raw)
        root_name = root_match.group(1) if root_match else ""
        ac_tables = set()
        if root_name:
            ac_tables = set(re.findall(r'<AdditionalColumns table="' + re.escape(root_name) + r'\.(\w+)"', raw))
        if ac_tables:
            owner_key = ctx.split('.Form.')[0]
            owner_ts = borrowed_ts_index.get(owner_key, {})
            for tbl_name in sorted(ac_tables):
                dep_check_count += 1
                if tbl_name not in owner_ts:
                    r.error(f'12. {ctx}: <AdditionalColumns table="{root_name}.{tbl_name}"> — TabularSection.{tbl_name} not borrowed in extension')
                    check12_ok = False

        for mi in missing_items:
            r.warn(f'12. {ctx}: references {mi} not borrowed in extension')
            check12_ok = False

    if len(borrowed_forms_with_tree) == 0:
        r.ok('12. Form dependencies: no borrowed forms with tree')
    elif check12_ok:
        r.ok(f'12. Form dependencies: {dep_check_count} references checked')

    if r.stopped:
        r.finalize(out_file)
        sys.exit(1)

    # --- Check 13: TypeLink with human-readable paths ---
    check13_ok = True
    type_link_count = 0

    for bf in borrowed_forms_with_tree:
        raw = bf['RawText']
        ctx = bf['Context']
        matches = re.findall(r'<TypeLink>\s*<xr:DataPath>Items\.[^<]*</xr:DataPath>', raw)
        if matches:
            type_link_count += len(matches)
            r.warn(f'13. {ctx}: {len(matches)} TypeLink(s) with human-readable Items.* DataPath (should be stripped)')
            check13_ok = False

    if len(borrowed_forms_with_tree) == 0:
        r.ok('13. TypeLink: no borrowed forms with tree')
    elif check13_ok:
        r.ok('13. TypeLink: clean')

    # --- Check 14: пути Объект.* заимствованных форм против конфигурации-источника ---
    # Требует -ConfigPath: отличить живой путь от висячего можно только по исходному объекту.
    # «Объект.Партнер» валиден и без заимствования реквизита (наследуется от базы), а «Объект.Товары.Артикул»
    # не разрешится нигде, если Артикул — не колонка ТЧ и не колонка из <Columns> самой формы.
    # Такой путь платформа отвергает на загрузке: «Неверный путь к данным».
    if not r.stopped and borrowed_forms_with_tree:
        if not config_path_arg:
            r.out('[INFO]  14. Пути Объект.* против конфигурации-источника не проверялись: не задан -ConfigPath')
        else:
            cfg_root = config_path_arg
            if not os.path.isabs(cfg_root):
                cfg_root = os.path.join(os.getcwd(), cfg_root)
            if os.path.exists(cfg_root) and not os.path.isdir(cfg_root):
                cfg_root = os.path.dirname(cfg_root)

            if not os.path.isfile(os.path.join(cfg_root, 'Configuration.xml')):
                r.warn(f"14. -ConfigPath '{config_path_arg}': Configuration.xml не найден — проверка путей пропущена")
            else:
                check14_ok = True
                path_check_count = 0

                for bf in borrowed_forms_with_tree:
                    raw = bf['RawText']
                    ctx = bf['Context']
                    # Корень путей — имя основного реквизита формы (см. проверку 12). Нет его ни в
                    # <Attributes> формы, ни в <BaseForm> — путей с корнем не бывает, проверять нечего.
                    root_match14 = MAIN_ATTR_RE.search(raw)
                    if root_match14 is None:
                        continue
                    root_name14 = root_match14.group(1)
                    # У динамического списка набор полей — результат его запроса, а не состав объекта:
                    # туда входят и стандартные поля списка (Ref, Date, DefaultPicture), и псевдонимы
                    # запроса. Сверять такие пути с ChildObjects объекта нельзя — будут ложные ошибки
                    # (корпусная проверка: 3383 таких сегмента на 1094 формах списка УТ).
                    if '>cfg:DynamicList<' in root_match14.group(0):
                        continue
                    owner_key = ctx.split('.Form.')[0]
                    owner_parts = owner_key.split('.', 1)
                    if len(owner_parts) < 2:
                        continue
                    owner_type, owner_name = owner_parts
                    owner_dir = CHILD_TYPE_DIR_MAP.get(owner_type)
                    if not owner_dir:
                        continue
                    src_obj_file = os.path.join(cfg_root, owner_dir, f'{owner_name}.xml')
                    if not os.path.isfile(src_obj_file):
                        r.warn(f'14. {ctx}: объект-источник не найден в конфигурации ({owner_dir}/{owner_name}.xml)')
                        continue

                    # Имена, доступные первым сегментом пути: реквизиты и ТЧ объекта-источника.
                    # Плюс для каждой ТЧ — её колонки: второй сегмент проверяем по ним (именно там
                    # и жил дефект — Объект.Товары.Артикул при живой ТЧ Товары).
                    src_names = set()
                    src_ts_columns = {}
                    src_tree = etree.parse(src_obj_file, etree.XMLParser(remove_blank_text=True))
                    src_obj_el = None
                    for c in src_tree.getroot():
                        if isinstance(c.tag, str):
                            src_obj_el = c
                            break
                    src_child_objects = src_obj_el.find(f'{{{MD}}}ChildObjects') if src_obj_el is not None else None
                    if src_child_objects is not None:
                        for sub in src_child_objects:
                            if not isinstance(sub.tag, str):
                                continue
                            sub_ln = etree.QName(sub.tag).localname
                            # У регистра дочерние объекты — Dimension/Resource, а не Attribute: без них
                            # замена корня превратила бы тихий пропуск в ложные ошибки на форме записи.
                            if sub_ln not in ('Attribute', 'Dimension', 'Resource', 'TabularSection'):
                                continue
                            name_el = sub.find(f'{{{MD}}}Properties/{{{MD}}}Name')
                            if name_el is None or not name_el.text:
                                continue
                            sub_name = name_el.text.strip()
                            src_names.add(sub_name)
                            if sub_ln != 'TabularSection':
                                continue
                            cols = set()
                            for col_name in sub.findall(f'{{{MD}}}ChildObjects/{{{MD}}}Attribute/{{{MD}}}Properties/{{{MD}}}Name'):
                                if col_name.text:
                                    cols.add(col_name.text.strip())
                            src_ts_columns[sub_name] = cols
                    # Плюс колонки, объявленные в самой форме через <Columns>/<AdditionalColumns table="Объект.X">
                    root_pat14 = re.escape(root_name14)
                    for acm in re.finditer(r'<AdditionalColumns table="' + root_pat14 + r'\.(\w+)">(.*?)</AdditionalColumns>', raw, re.DOTALL):
                        tbl = acm.group(1)
                        cols = src_ts_columns.setdefault(tbl, set())
                        for cm in re.finditer(r'<Column name="(\w+)"', acm.group(2)):
                            cols.add(cm.group(1))

                    bad_paths = {}
                    for m in re.finditer(r'<(?:\w+:)?\w*DataPath[^>]*>' + root_pat14 + r'\.([^<]+)</(?:\w+:)?\w*DataPath>', raw):
                        segments = m.group(1).split('.')
                        seg0 = segments[0]
                        path_check_count += 1
                        if seg0 in STANDARD_OBJECT_FIELDS:
                            continue
                        if seg0 not in src_names:
                            bad_paths[f'{root_name14}.{seg0}'] = f'у {owner_key} нет такого реквизита или табличной части'
                            continue
                        # Второй сегмент проверяем только для табличных частей: у ссылочного реквизита
                        # он ведёт в чужой объект, и это уже другая проверка.
                        if len(segments) < 2 or seg0 not in src_ts_columns:
                            continue
                        seg1 = segments[1]
                        if seg1 in STANDARD_OBJECT_FIELDS:
                            continue
                        # Итог колонки — псевдополе платформы: Total<Колонка> при живой колонке законен
                        if seg1.startswith('Total') and seg1[5:] in src_ts_columns[seg0]:
                            continue
                        if seg1 not in src_ts_columns[seg0]:
                            bad_paths[f'{root_name14}.{seg0}.{seg1}'] = f'у табличной части {seg0} нет колонки {seg1}, и <Columns> формы её не объявляет'

                    for bad in sorted(bad_paths):
                        r.error(f"14. {ctx}: путь '{bad}' — {bad_paths[bad]}")
                        check14_ok = False

                if check14_ok:
                    r.ok(f'14. Object paths vs source config: {path_check_count} checked')

    # --- Check 15: основные роли расширения не дают прав на заимствованные объекты ---
    # Платформа: «Назначение прав доступа на заимствованные объекты основными ролями в
    # расширениях недопустимо». Роль вне <DefaultRoles> так делать вправе — проверяем только
    # основные. Ловится статически, а по симптому (отказ загрузки) причина не читается.
    default_role_nodes = cfg_node.findall('md:Properties/md:DefaultRoles/xr:Item', NS)
    if default_role_nodes:
        adopted_cache = {}

        def is_object_adopted(type_name, obj_name):
            key = f"{type_name}.{obj_name}"
            if key in adopted_cache:
                return adopted_cache[key]
            adopted_cache[key] = False
            dir_name = CHILD_TYPE_DIR_MAP.get(type_name)
            if dir_name:
                obj_path = os.path.join(config_dir, dir_name, obj_name + '.xml')
                if os.path.isfile(obj_path):
                    try:
                        obj_root = etree.parse(obj_path).getroot()
                        ob = obj_root.find(f'md:{type_name}/md:Properties/md:ObjectBelonging', NS)
                        if ob is not None and (ob.text or '') == 'Adopted':
                            adopted_cache[key] = True
                    except Exception:
                        pass
            return adopted_cache[key]

        check15_ok = True
        check15_count = 0
        roles_ns = {'r': 'http://v8.1c.ru/8.2/roles'}
        for rn in default_role_nodes:
            m = re.match(r'^Role\.(.+)$', rn.text or '')
            if not m:
                continue
            def_role_name = m.group(1)
            rights_path = os.path.join(config_dir, 'Roles', def_role_name, 'Ext', 'Rights.xml')
            if not os.path.isfile(rights_path):
                continue
            try:
                rights_root = etree.parse(rights_path).getroot()
            except Exception:
                continue
            for name_node in rights_root.findall('r:object/r:name', roles_ns):
                full_name = name_node.text or ''
                segs = full_name.split('.')
                # Configuration.* — права самого расширения, не объект; заимствования там нет.
                if len(segs) < 2 or segs[0] == 'Configuration':
                    continue
                check15_count += 1
                if is_object_adopted(segs[0], segs[1]):
                    r.error(f"15. Роль '{def_role_name}' входит в DefaultRoles и даёт права на заимствованный "
                            f"{segs[0]}.{segs[1]} ({full_name}): платформа это запрещает. "
                            "Вынесите такие права в отдельную роль вне DefaultRoles.")
                    check15_ok = False
        if check15_ok and check15_count > 0:
            r.ok(f'15. Основные роли: прав на заимствованные объекты нет ({check15_count} checked)')

    if r.stopped:
        r.finalize(out_file)
        sys.exit(1)

    # --- Check 16: модуль заимствованного объекта и пометка расширенного свойства ---
    # Свойство <xr:PropertyState> появилось в формате 2.19 (8.3.26); ниже платформа его молча
    # выбрасывает, поэтому там проверять нечего. С 2.19 состояние обязано соответствовать факту:
    # есть файл модуля — есть пометка, и наоборот. Перекос платформа принимает (проверено на
    # стенде), но выгрузка Конфигуратора так не выглядит — отсюда предупреждение, а не ошибка.
    if version_rank >= 219 and child_obj_node is not None:
        state_issues = []
        state_checked = 0
        for child in child_obj_node:
            if not isinstance(child.tag, str):
                continue
            type_name = etree.QName(child.tag).localname
            if type_name not in MODULE_KINDS_BY_TYPE or type_name not in CHILD_TYPE_DIR_MAP:
                continue
            obj_name_val = (child.text or '').strip()
            if not obj_name_val:
                continue
            type_dir = os.path.join(config_dir, CHILD_TYPE_DIR_MAP[type_name])
            obj_file = os.path.join(type_dir, f'{obj_name_val}.xml')
            if not os.path.isfile(obj_file):
                continue
            with open(obj_file, 'r', encoding='utf-8-sig') as f:
                obj_text = f.read()
            if '<ObjectBelonging>Adopted</ObjectBelonging>' not in obj_text:
                continue

            for kind in MODULE_KINDS_BY_TYPE[type_name]:
                state_checked += 1
                has_file = os.path.isfile(os.path.join(type_dir, obj_name_val, 'Ext', f'{kind}.bsl'))
                has_flag = f'<xr:Property>{kind}</xr:Property>' in obj_text
                if has_file and not has_flag:
                    state_issues.append(f'{type_name}.{obj_name_val} — есть {kind}.bsl, но нет <xr:PropertyState> для {kind}')
                elif has_flag and not has_file:
                    state_issues.append(f'{type_name}.{obj_name_val} — есть <xr:PropertyState> для {kind}, но нет {kind}.bsl')

        if state_checked > 0:
            if not state_issues:
                r.ok(f'16. Модули заимствованных объектов: пометки расширенных свойств согласованы ({state_checked})')
            else:
                for issue in state_issues:
                    r.warn(f'16. {issue}')

    # --- Breadcrumb: controlled methods (&ИзменениеИКонтроль) drift is not checked here ---
    ctrl_count = 0
    for dp, _dn, files in os.walk(config_dir):
        for fn in files:
            if fn.endswith('.bsl'):
                try:
                    bsl_path = os.path.join(dp, fn)
                    with open(bsl_path, 'r', encoding='utf-8-sig') as f:
                        bsl_text = f.read()
                    ctrl_count += len(re.findall(r'^\s*&ИзменениеИКонтроль\(', bsl_text, re.M))
                    if re.search(r'\b(?:Список|ДинамическийСписок)\w*\.ТекущаяСтрока\b', bsl_text, re.I):
                        relative_bsl = os.path.relpath(bsl_path, config_dir)
                        r.warn(f'17. {relative_bsl}: suspicious DynamicList.ТекущаяСтрока; use form table element .ТекущиеДанные or verify the runtime type')
                except OSError:
                    pass
    if ctrl_count > 0:
        r.out('[INFO]  Контролируемых методов (&ИзменениеИКонтроль): %d — их актуальность здесь не проверяется. Сверьте: /cfe-patch-method -Check -ExtensionPath <ext> -ConfigPath <cf>' % ctrl_count)

    # --- Final output ---
    r.finalize(out_file)
    sys.exit(1 if r.errors > 0 else 0)


if __name__ == '__main__':
    main()
