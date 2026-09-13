#!/usr/bin/env python3
# db-run v1.10 — Launch 1C:Enterprise
# Source: https://github.com/Nikolay-Shirokov/cc-1c-skills

import argparse
import ctypes
import glob
import json
import os
import re
import subprocess
import sys
import time


_LOADING_WINDOW_TITLES = (
    "Загрузка конфигурационной информации",
    "Loading configuration information",
)


def _window_is_ready(title, responding, reject_loading_title=True):
    """Mirror the PowerShell readiness gate: titled, responding GUI window.

    The initial wait also rejects the known 1C loading title. The grace wait keeps
    the PowerShell behavior and only requires a responding titled window because
    it is entered after a normal application title has already been observed.
    """
    return bool(
        title
        and responding
        and (
            not reject_loading_title
            or not any(marker in title for marker in _LOADING_WINDOW_TITLES)
        )
    )


def _window_responds(send_message_timeout, hwnd, timeout_ms=500):
    """Probe a Win32 window message loop without waiting indefinitely.

    ``Process.Responding`` in the PowerShell port performs the same kind of GUI
    responsiveness check. A visible window and a normal title alone are not
    enough: a hung 1C client must remain in the readiness wait and time out.
    """
    message_result = ctypes.c_size_t()
    # WM_NULL asks the target message loop to process a harmless message.
    # SMTO_BLOCK | SMTO_ABORTIFHUNG bounds the call when the target is hung.
    return bool(
        send_message_timeout(
            hwnd,
            0x0000,
            0,
            0,
            0x0001 | 0x0002,
            timeout_ms,
            ctypes.byref(message_result),
        )
    )


def _select_window_state(
    windows,
    send_message_timeout,
    reject_loading_title=True,
):
    """Evaluate every titled window and select readiness/diagnostic state.

    1C can own more than one top-level window while starting. Enumeration order
    is not a main-window guarantee, so a hung splash/modal must not hide a later
    responsive application window. When none is ready, prefer a normal title for
    grace/timeout diagnostics, then a responsive loading title, then the first
    remaining window.
    """
    states = [
        (hwnd, title, _window_responds(send_message_timeout, hwnd))
        for hwnd, title in windows
    ]
    if not states:
        return 0, "", False

    def priority(state):
        _, title, responding = state
        loading = any(marker in title for marker in _LOADING_WINDOW_TITLES)
        ready = _window_is_ready(title, responding, reject_loading_title)
        return (
            0 if ready else 1,
            0 if not loading else 1,
            0 if responding else 1,
        )

    return min(states, key=priority)

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



def _find_project_v8path():
    """Walk up from CWD to find .v8-project.json and read its v8path."""
    d = os.getcwd()
    while True:
        pf = os.path.join(d, ".v8-project.json")
        if os.path.isfile(pf):
            try:
                with open(pf, encoding="utf-8-sig") as f:
                    data = json.load(f)
                v = data.get("v8path")
                if v:
                    return v
            except Exception:
                pass
            return None
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


# --- Additional platform arguments ---
V8_OWNED_KEYS = [
    "DESIGNER", "ENTERPRISE", "CREATEINFOBASE", "CONFIG",
    "/F", "/S", "/N", "/P", "/Out", "/DisableStartupDialogs",
    "/UseTemplate", "/AddToList", "/Execute", "/C", "/URL", "/UC",
    "/DumpIB", "/RestoreIB", "/DumpCfg", "/LoadCfg",
    "/DumpConfigToFiles", "/LoadConfigFromFiles", "/UpdateDBCfg",
    "/DumpExternalDataProcessorOrReportToFiles", "/LoadExternalDataProcessorOrReportFromFiles",
]
# Пакетные команды платформы. В одной командной строке DESIGNER выполняет ТОЛЬКО ПОСЛЕДНЮЮ,
# остальные молча отбрасывает (проверено на 8.3.24: /LoadConfigFromFiles вместе с
# /CheckCanApplyConfigurationExtensions завершились кодом 0 с пустым логом, и загрузка НЕ
# состоялась). Такая команда в дополнительных аргументах подменяет собой операцию навыка, а навык
# отчитывается успехом. Дополнительные аргументы — это опции, а не режимы.
V8_BATCH_KEYS = [
    "/CheckConfig", "/CheckModules", "/CheckCanApplyConfigurationExtensions",
    "/DumpDBCfgList", "/DeleteCfg", "/UpdateCfg", "/CompareCfg", "/MergeCfg",
    "/ManageCfgSupport", "/RollbackCfg", "/ConvertFiles",
]

IBCMD_OWNED_KEYS = [
    "--db-path", "--data", "--out", "--file", "--load", "--restore",
    "--import", "--export", "--apply", "--force", "--create-database",
    "--user", "--password",
]
V8_SECRET_KEYS = ["/P", "/UC", "/WSP", "/AWSP"]
IBCMD_SECRET_KEYS = ["--password", "--token", "--db-pwd"]


def arg_key_match(token, key):
    """Token matches a key when it equals it, or starts with it and the next character
    is not a letter — catches glued /N"user" and --password=x, while keeping
    /ClearCache distinct from /C."""
    if len(token) < len(key):
        return False
    if token[: len(key)].lower() != key.lower():
        return False
    if len(token) == len(key):
        return True
    return not token[len(key)].isalpha()


def project_extra_args(name):
    """v8args / ibcmdargs from .v8-project.json — same upward walk as v8path."""
    d = os.getcwd()
    while True:
        pf = os.path.join(d, ".v8-project.json")
        if os.path.isfile(pf):
            try:
                with open(pf, encoding="utf-8-sig") as f:
                    data = json.load(f)
                v = data.get(name)
                if v:
                    return [str(x) for x in v]
            except Exception:
                pass
            return []
        parent = os.path.dirname(d)
        if parent == d:
            return []
        d = parent


def assert_extra_args(extra, engine, hints):
    """The platform accepts only one batch operation, and a duplicate connection or
    output key fails with an opaque 1C error — reject what the skill owns itself."""
    param = "-AdditionalIbcmdArguments" if engine == "ibcmd" else "-AdditionalV8Arguments"
    owned = IBCMD_OWNED_KEYS if engine == "ibcmd" else V8_OWNED_KEYS
    for tok in extra:
        if engine == "ibcmd" and not tok.startswith("-"):
            print(
                f"Error: '{tok}' is a positional token — pass values as --key=value "
                f"({param} cannot extend the ibcmd command)",
            )
            sys.exit(1)
        if engine != "ibcmd":
            for b in V8_BATCH_KEYS:
                if arg_key_match(tok, b):
                    print(
                        f"Error: {b} is a batch command; passed via {param} it would replace "
                        f"the skill's own operation (a command line runs only its last batch command)",
                    )
                    sys.exit(1)
        for k in owned:
            if arg_key_match(tok, k):
                hint = f" (use {hints[k]})" if hints and k in hints else ""
                print(
                    f"Error: {k} is controlled by the skill and cannot be passed via {param}{hint}",
                )
                sys.exit(1)


def format_args_for_display(arglist, engine):
    """Redact values of secret-prone keys in glued, =-joined and separate forms.
    Matching here is a plain prefix (no letter rule): over-masking costs nothing,
    a leaked password does."""
    keys = IBCMD_SECRET_KEYS if engine == "ibcmd" else V8_SECRET_KEYS
    res = []
    mask_next = False
    for tok in arglist:
        if mask_next:
            res.append("***")
            mask_next = False
            continue
        hit = None
        for k in keys:
            if tok[: len(k)].lower() == k.lower():
                hit = k
                break
        if hit is None:
            res.append(tok)
        elif len(tok) == len(hit):
            res.append(tok)
            mask_next = True
        elif tok[len(hit)] == "=":
            res.append(hit + "=***")
        else:
            res.append(hit + "***")
    return res


def extract_extra_args(argv, known_opts):
    """argparse refuses values that start with '-' (every ibcmd key does), so pull the two
    escape-hatch lists out of argv by hand: after the flag, take everything up to the next
    declared skill option. Returns (remaining_argv, v8_extra, ibcmd_extra)."""
    rest, v8, ibcmd = [], [], []
    i = 0
    while i < len(argv):
        low = argv[i].lower()
        if low in ("-additionalv8arguments", "-additionalibcmdarguments"):
            target = v8 if low == "-additionalv8arguments" else ibcmd
            i += 1
            while i < len(argv) and argv[i].lower() not in known_opts:
                target.append(argv[i])
                i += 1
            continue
        rest.append(argv[i])
        i += 1
    return rest, v8, ibcmd


def resolve_extra_args(engine, v8_extra, ibcmd_extra, hints):
    """Pick the argument list for the selected engine and validate it. An explicitly
    passed parameter for the other engine is an error; the same keys coming from
    .v8-project.json simply do not apply — a project may describe both engines.

    Comma-separated elements are split apart: PowerShell's -File cannot bind an array,
    so that form is the documented one and both ports must accept it. A value containing
    a comma is not supported."""
    v8_extra = [p for tok in v8_extra for p in str(tok).split(",") if p]
    ibcmd_extra = [p for tok in ibcmd_extra for p in str(tok).split(",") if p]
    if engine == "ibcmd" and v8_extra:
        print(
            "Error: -AdditionalV8Arguments applies to 1cv8 only; the selected engine is ibcmd "
            "(use -AdditionalIbcmdArguments)",
        )
        sys.exit(1)
    if engine != "ibcmd" and ibcmd_extra:
        print(
            "Error: -AdditionalIbcmdArguments applies to ibcmd only; the selected engine is 1cv8 "
            "(use -AdditionalV8Arguments)",
        )
        sys.exit(1)
    if engine == "ibcmd":
        extra = project_extra_args("ibcmdargs") + list(ibcmd_extra)
    else:
        extra = project_extra_args("v8args") + list(v8_extra)
    if extra:
        assert_extra_args(extra, engine, hints)
    return extra


def clean_path(value, param=""):
    """Forgive what is unambiguous in a path the caller passed: surrounding whitespace,
    surrounding quotes that survived shell parsing, a trailing separator. A quote left
    inside afterwards cannot be part of a real path — reject it by name instead of letting
    1C answer with its opaque "Неверные или отсутствующие параметры соединения"."""
    if not value:
        return value
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1].strip()
    if len(v) > 3 and v[-1] in "\\/":
        v = v[:-1]
    if '"' in v:
        print(f"Error: {param or 'path'} contains a quote character: {value}")
        sys.exit(1)
    return v


def _version_dir(p):
    """Version dir for both Windows (.../1cv8/<ver>/bin/1cv8.exe) and *nix (.../1cv8/<ver>/1cv8)."""
    parent = os.path.dirname(p)
    if os.path.basename(parent).lower() == "bin":
        parent = os.path.dirname(parent)
    return os.path.basename(parent)


def _version_key(p):
    """Numeric sort key from version dir name."""
    return [int(x) for x in re.findall(r"\d+", _version_dir(p))]


def resolve_v8path(v8path):
    """Resolve path to a 1C executable (1cv8; ibcmd only when given explicitly)."""
    if not v8path:
        v8path = _find_project_v8path()
    if not v8path:
        if os.name == "nt":
            candidates = (
                glob.glob(r"C:\Program Files\1cv8\*\bin\1cv8.exe")
                + glob.glob(r"C:\Program Files (x86)\1cv8\*\bin\1cv8.exe")
            )
        else:
            # PY-only: PS-порт на *nix не исполняется, поэтому *nix-раскладки нет в .ps1.
            candidates = glob.glob("/opt/1cv8/*/1cv8")
        if candidates:
            v8path = max(candidates, key=_version_key)
            print(f"Auto-selected platform {_version_dir(v8path)}: {v8path}")
        else:
            print("Error: 1C executable not found. Specify -V8Path")
            sys.exit(1)
    if os.path.isdir(v8path):
        # PY-only: на *nix исполняемый называется "1cv8" (без .exe); ibcmd — только явным путём.
        exe = "1cv8.exe" if os.name == "nt" else "1cv8"
        v8path = os.path.join(v8path, exe)
    if not os.path.isfile(v8path):
        print(f"Error: 1C executable not found at {v8path}")
        sys.exit(1)
    return v8path


def _redact(text, *secrets):
    """Redact literal secret values (password, user) from a display string —
    precise, never touches lookalike paths."""
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Launch 1C:Enterprise",
        allow_abbrev=False,
    )
    parser.add_argument("-V8Path", default="")
    parser.add_argument("-InfoBasePath", default="")
    parser.add_argument("-InfoBaseServer", default="")
    parser.add_argument("-InfoBaseRef", default="")
    parser.add_argument("-UserName", default="")
    parser.add_argument("-Password", default="")
    parser.add_argument("-Execute", default="")
    parser.add_argument("-CParam", default="")
    parser.add_argument("-URL", default="")
    parser.add_argument("-Client", choices=["Thick", "Thin"], default="Thick")
    parser.add_argument("-Visible", action="store_true")
    parser.add_argument("-SingleInstance", action="store_true")
    parser.add_argument("-WaitReadySeconds", type=int, default=0)
    parser.add_argument("-ReadyGraceSeconds", type=int, default=30)
    parser.add_argument("-AdditionalV8Arguments", nargs="*", default=[],
                        help="Extra 1cv8 arguments, e.g. /UseHwLicenses+")
    parser.add_argument("-AdditionalIbcmdArguments", nargs="*", default=[],
                        help="Extra ibcmd arguments in --key=value form")
    known_opts = {s.lower() for a in parser._actions for s in a.option_strings}
    argv, v8_extra, ibcmd_extra = extract_extra_args(sys.argv[1:], known_opts)
    args = ci_parse_args(parser, argv)

    args.V8Path = clean_path(args.V8Path, "-V8Path")
    args.InfoBasePath = clean_path(args.InfoBasePath, "-InfoBasePath")
    args.Execute = clean_path(args.Execute, "-Execute")

    v8path = resolve_v8path(args.V8Path)
    launch_exe = v8path
    if args.Client == "Thin":
        launch_exe = os.path.join(os.path.dirname(v8path), "1cv8c.exe" if os.name == "nt" else "1cv8c")
        if not os.path.isfile(launch_exe):
            print(f"Error: thin client executable not found at {launch_exe}")
            sys.exit(1)

    # --- Resolve additional arguments ---
    # 1C:Enterprise is always launched by 1cv8 — ibcmd has no interactive mode.
    engine = "1cv8"
    arg_hints = {
        "/F": "-InfoBasePath",
        "/S": "-InfoBaseServer + -InfoBaseRef",
        "/N": "-UserName",
        "/P": "-Password",
        "/Execute": "-Execute",
        "/C": "-CParam",
        "/URL": "-URL",
    }
    extra_args = resolve_extra_args(engine, v8_extra, ibcmd_extra, arg_hints)

    # --- Validate connection ---
    if not args.InfoBasePath and (not args.InfoBaseServer or not args.InfoBaseRef):
        print("Error: specify -InfoBasePath or -InfoBaseServer + -InfoBaseRef")
        sys.exit(1)

    # --- Build arguments ---
    arguments = ["ENTERPRISE"]

    if args.InfoBaseServer and args.InfoBaseRef:
        arguments.extend(["/S", f"{args.InfoBaseServer}/{args.InfoBaseRef}"])
    else:
        arguments.extend(["/F", args.InfoBasePath])

    if args.UserName:
        arguments.append(f"/N{args.UserName}")
    if args.Password:
        arguments.append(f"/P{args.Password}")

    # --- Optional params ---
    execute = args.Execute
    if execute:
        ext = os.path.splitext(execute)[1].lower()
        if ext == ".erf":
            print("[WARN] /Execute does not support ERF files (external reports).")
            print(f"       Open the report via File -> Open: {execute}")
            print("       Launching database without /Execute.")
            execute = ""

    if execute:
        arguments.extend(["/Execute", execute])
    if args.CParam:
        arguments.extend(["/C", args.CParam])
    if args.URL:
        arguments.extend(["/URL", args.URL])

    if not args.Visible:
        arguments.append("/DisableStartupDialogs")
    arguments.extend(extra_args)

    # --- Execute (background) ---
    # Redact the password/user before printing the command line — never leak secrets.
    if args.SingleInstance and os.name == "nt":
        needle = args.InfoBasePath or f"{args.InfoBaseServer}/{args.InfoBaseRef}"
        check = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
            "$n=$args[0]; @(Get-CimInstance Win32_Process -Filter \"Name='1cv8.exe' OR Name='1cv8c.exe'\" | Where-Object {$_.CommandLine -and $_.CommandLine.IndexOf($n,[StringComparison]::OrdinalIgnoreCase)-ge 0}).Count", needle],
            capture_output=True, text=True)
        if check.returncode == 0 and check.stdout.strip() not in ("", "0"):
            print("Error: 1C client for this infobase is already running")
            sys.exit(2)
    print(f"Running: {os.path.basename(launch_exe)} {_redact(' '.join(format_args_for_display(arguments, engine)), args.Password, args.UserName)}")
    proc = subprocess.Popen([launch_exe] + arguments)

    # --- Bounded early-exit check ---
    # The launch is a background GUI process, so we don't wait for completion. But a process
    # that dies within the first ~1.5s never really started (bad base, no display, license) —
    # report that honestly instead of a blind "launched".
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(0.2)
    rc = proc.poll()
    if rc is not None:
        print(f"Error: 1C:Enterprise exited immediately (code: {rc})")
        sys.exit(rc if rc and rc > 0 else 1)
    print(f"PID: {proc.pid}")
    if args.WaitReadySeconds > 0:
        if os.name != "nt":
            print("Error: -WaitReadySeconds currently requires Windows")
            sys.exit(3)
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HWND,
            wintypes.LPARAM,
        )
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.SendMessageTimeoutW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t

        def window_state(pid, reject_loading_title=True):
            found = []
            def callback(hwnd, _):
                owner = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
                if owner.value == pid and user32.IsWindowVisible(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    if buf.value:
                        found.append((hwnd, buf.value))
                return True
            user32.EnumWindows(callback_type(callback), 0)
            return _select_window_state(
                found,
                user32.SendMessageTimeoutW,
                reject_loading_title,
            )
        deadline = time.monotonic() + args.WaitReadySeconds
        title = ""
        while time.monotonic() < deadline:
            rc = proc.poll()
            if rc is not None:
                print(f"Error: 1C:Enterprise exited before becoming ready (code: {rc})")
                sys.exit(rc if rc else 1)
            _, title, responding = window_state(proc.pid)
            if _window_is_ready(title, responding):
                print(f"1C:Enterprise ready: {title}")
                break
            time.sleep(0.5)
        else:
            if args.ReadyGraceSeconds > 0 and title and not any(marker in title for marker in _LOADING_WINDOW_TITLES):
                print(f"[WARN] Normal application title appeared near timeout; waiting {args.ReadyGraceSeconds} extra seconds for readiness.")
                grace_deadline = time.monotonic() + args.ReadyGraceSeconds
                while time.monotonic() < grace_deadline and proc.poll() is None:
                    _, title, responding = window_state(
                        proc.pid,
                        reject_loading_title=False,
                    )
                    if _window_is_ready(title, responding, reject_loading_title=False):
                        print(f"1C:Enterprise ready: {title}")
                        break
                    time.sleep(0.5)
                else:
                    print(f"Error: client did not become ready within {args.WaitReadySeconds}+{args.ReadyGraceSeconds} seconds (PID: {proc.pid}, title: '{title}')")
                    sys.exit(3)
            else:
                print(f"Error: client did not become ready within {args.WaitReadySeconds} seconds (PID: {proc.pid}, title: '{title}')")
                sys.exit(3)
    else:
        print("1C:Enterprise launched")


if __name__ == "__main__":
    main()
