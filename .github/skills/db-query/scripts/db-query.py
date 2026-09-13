#!/usr/bin/env python3
"""Read-only 1C query through COMConnector."""

import argparse
import csv
import gc
import io
import json
import os
import sys
from datetime import date, datetime

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


def fail(message):
    print(f"Error: {message}")
    raise SystemExit(1)


def clean_path(value, name):
    if not value:
        return value
    clean = value.strip().strip('"').strip("'")
    if '"' in clean:
        fail(f"{name} contains a quote character")
    return clean


def normalize_value(value, connection):
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    try:
        return str(connection.String(value))
    except Exception:
        return str(value)


parser = argparse.ArgumentParser(allow_abbrev=False)
parser.add_argument('-InfoBasePath')
parser.add_argument('-InfoBaseServer')
parser.add_argument('-InfoBaseRef')
parser.add_argument('-UserName')
parser.add_argument('-Password')
parser.add_argument('-QueryFile')
parser.add_argument('-QueryText')
parser.add_argument('-ParametersJson')
parser.add_argument('-OutputFile')
parser.add_argument('-OutputFormat', choices=['Json', 'Csv'], default='Json')
parser.add_argument('-MaxRows', type=int, default=10000)
parser.add_argument('-ComProgId')
args = parser.parse_args()

args.InfoBasePath = clean_path(args.InfoBasePath, '-InfoBasePath')
args.QueryFile = clean_path(args.QueryFile, '-QueryFile')
args.ParametersJson = clean_path(args.ParametersJson, '-ParametersJson')
args.OutputFile = clean_path(args.OutputFile, '-OutputFile')

file_connection = bool(args.InfoBasePath)
server_connection = bool(args.InfoBaseServer or args.InfoBaseRef)
if file_connection == server_connection:
    fail('specify either -InfoBasePath or both -InfoBaseServer and -InfoBaseRef')
if server_connection and not (args.InfoBaseServer and args.InfoBaseRef):
    fail('-InfoBaseServer and -InfoBaseRef must be specified together')
if file_connection and not os.path.isdir(args.InfoBasePath):
    fail(f'infobase directory not found: {args.InfoBasePath}')
if args.MaxRows < 1 or args.MaxRows > 1000000:
    fail('-MaxRows must be between 1 and 1000000')

has_query_file = bool(args.QueryFile)
has_query_text = bool(args.QueryText)
if has_query_file == has_query_text:
    fail('specify exactly one of -QueryFile or -QueryText')
if has_query_file:
    if not os.path.isfile(args.QueryFile):
        fail(f'query file not found: {args.QueryFile}')
    with open(args.QueryFile, 'r', encoding='utf-8-sig') as stream:
        args.QueryText = stream.read()
if not args.QueryText or not args.QueryText.strip():
    fail('query text is empty')

parameters = {}
if args.ParametersJson:
    if not os.path.isfile(args.ParametersJson):
        fail(f'parameters file not found: {args.ParametersJson}')
    try:
        with open(args.ParametersJson, 'r', encoding='utf-8-sig') as stream:
            parameters = json.load(stream)
    except Exception as exc:
        fail(f'invalid parameters JSON: {exc}')
    if not isinstance(parameters, dict):
        fail('parameters JSON must contain an object')
    for name, value in parameters.items():
        if value is not None and not isinstance(value, (str, bool, int, float)):
            fail(f"parameter '{name}' must be a scalar JSON value")

connection_values = [args.InfoBasePath, args.InfoBaseServer, args.InfoBaseRef, args.UserName, args.Password]
if any(value and '"' in value for value in connection_values):
    fail('connection values cannot contain quote characters')
if file_connection:
    connection_string = f'File="{args.InfoBasePath}";'
else:
    connection_string = f'Srvr="{args.InfoBaseServer}";Ref="{args.InfoBaseRef}";'
if args.UserName:
    connection_string += f'Usr="{args.UserName}";'
if args.Password:
    connection_string += f'Pwd="{args.Password}";'

pythoncom = None
connector = connection = query = result = columns_collection = selection = None
try:
    if sys.platform != 'win32':
        fail('COMConnector is available only on Windows')
    try:
        import pythoncom as pythoncom_module
        import win32com.client
        pythoncom = pythoncom_module
    except ImportError:
        fail('pywin32 is required: pip install pywin32')

    pythoncom.CoInitialize()
    connector = None
    errors = []
    prog_ids = [args.ComProgId] if args.ComProgId else ['V85.COMConnector', 'V83.COMConnector']
    for prog_id in prog_ids:
        try:
            connector = win32com.client.Dispatch(prog_id)
            break
        except Exception as exc:
            errors.append(f'{prog_id}: {exc}')
    if connector is None:
        fail(f"COMConnector is not registered ({'; '.join(errors)})")

    connection = connector.Connect(connection_string)
    query = connection.NewObject('Query')
    query.Text = args.QueryText
    for name in sorted(parameters):
        query.SetParameter(name, parameters[name])

    result = query.Execute()
    # pywin32 exposes these two 1C COM properties as properties; PowerShell's
    # late binder exposes the same dispatch members as zero-argument calls.
    columns_collection = result.Columns
    column_names = [str(columns_collection.Get(index).Name) for index in range(columns_collection.Count())]
    selection = result.Select()
    rows = []
    truncated = False
    while selection.Next():
        if len(rows) >= args.MaxRows:
            truncated = True
            break
        rows.append({name: normalize_value(selection.Get(index), connection) for index, name in enumerate(column_names)})

    if args.OutputFormat == 'Csv':
        buffer = io.StringIO(newline='')
        if rows:
            writer = csv.DictWriter(buffer, fieldnames=column_names)
            writer.writeheader()
            writer.writerows(rows)
        text = buffer.getvalue()
    else:
        text = json.dumps({
            'ok': True,
            'columns': column_names,
            'rowCount': len(rows),
            'truncated': truncated,
            'rows': rows,
        }, ensure_ascii=False, indent=2)

    if args.OutputFile:
        full_output = os.path.abspath(args.OutputFile)
        parent = os.path.dirname(full_output)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(full_output, 'w', encoding='utf-8-sig', newline='') as stream:
            stream.write(text)
            if not text.endswith('\n'):
                stream.write('\n')
        print(f'[OK] Query returned {len(rows)} row(s); output: {full_output}')
        if truncated:
            print(f'[WARN] Result truncated at {args.MaxRows} row(s)')
    else:
        print(text)
except SystemExit:
    raise
except Exception as exc:
    message = str(exc)
    if args.Password:
        message = message.replace(args.Password, '***')
    fail(message)
finally:
    if pythoncom is not None:
        try:
            selection = None
            columns_collection = None
            result = None
            query = None
            connection = None
            connector = None
            gc.collect()
            pythoncom.CoUninitialize()
        except Exception:
            pass
