import json 
import sys
from pathlib import Path
from datetime import datetime

# --- CONFIGURATION ---
TIMESTAMP_FORMAT = "%H:%M:%S"

class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    # New Colors for "Beautiful" UI
    GREY = '\033[90m'      # For timestamps (subtle)
    WHITE = '\033[97m'     # For high contrast text
    BG_BLUE = '\033[44m'   # For headers

def _get_timestamp():
    """Returns a dimmed timestamp string."""
    now = datetime.now().strftime(TIMESTAMP_FORMAT)
    return f"{bcolors.GREY}[{now}]{bcolors.ENDC}"

def _print_formatted(icon: str, text: str, color: str, level: int = 1, newline: bool = True):
    """
    Central logic for all printed lines to ensure consistent alignment and timestamping.
    Layout: [TIMESTAMP]  [INDENT]ICON   TEXT
    """
    ts = _get_timestamp()
    indent = "  " * (level - 1)
    
    # FIX: Force icon column to be exactly 4 characters wide (Icon + Spaces)
    # This prevents '🛡️' (wide) vs 'ℹ️' (narrow) from shifting the text.
    # Note: Some emojis are 2 chars wide, some 1. We pad generously.
    formatted_icon = f"{icon:<4}" 
    
    # Construct the final string
    msg = f"{ts} {indent}{formatted_icon} {color}{text}{bcolors.ENDC}"
    
    if newline:
        print(msg)
    else:
        print(msg, end="", flush=True)

# --- PUBLIC API ---

def print_header(text: str):
    """Prints a distinct, modern header block."""
    print(f"\n{_get_timestamp()} {bcolors.BOLD}{bcolors.OKCYAN}┏{'━'*60}┓{bcolors.ENDC}")
    print(f"{_get_timestamp()} {bcolors.BOLD}{bcolors.OKCYAN}┃  {text.upper().ljust(56)}  ┃{bcolors.ENDC}")
    print(f"{_get_timestamp()} {bcolors.BOLD}{bcolors.OKCYAN}┗{'━'*60}┛{bcolors.ENDC}")

def print_stage(text: str, level: int = 1):
    _print_formatted("▶", text, bcolors.OKBLUE, level)

def print_success(text: str, level: int = 1):
    _print_formatted("✅", text, bcolors.OKGREEN, level)

def print_failure(text: str, level: int = 1):
    _print_formatted("❌", text, bcolors.FAIL, level)

def print_warning(text: str, level: int = 1):
    _print_formatted("⚠️", text, bcolors.WARNING, level)
    
def print_info(text: str, level: int = 1):
    _print_formatted("ℹ️", text, bcolors.OKCYAN, level)

def print_input_prompt(text: str):
    """Prints a prompt for input, keeping the cursor on the same line."""
    _print_formatted("👉", text, bcolors.WARNING, level=1, newline=False)
    # Reset color for the user's typing
    print(bcolors.ENDC, end=" ")
    return input()

# --- PRETTY JSON PRINTER ---
def pretty_print_json(data, indent=2) -> str:
    """Returns syntax-highlighted JSON string."""
    if data is None:
        return f"{bcolors.GREY}None{bcolors.ENDC}"
    try:
        if isinstance(data, str):
            data = json.loads(data)
        formatted_json = json.dumps(data, indent=indent)
        return f"{bcolors.OKCYAN}{formatted_json}{bcolors.ENDC}"
    except (json.JSONDecodeError, TypeError):
        return str(data)

# --- FILE SELECTION UI ---
def select_file(base_dir: Path, pattern: str = "*.*", num_to_show: int = 9, default_selection: str | None = None) -> Path | None:
    base_dir.mkdir(exist_ok=True)
    files = [f for f in base_dir.glob(pattern) if f.is_file()]
    
    if not files:
        print_failure(f"No files matching '{pattern}' found in '{base_dir}'.")
        return None

    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    
    # Identify default
    default_index = -1
    if default_selection:
        for i, f in enumerate(files):
            if f.name == default_selection:
                default_index = i
                break
    
    display_files = files[:num_to_show]
    
    print_header(f"SELECT FILE ({pattern})")
    
    # Calculate column widths for perfect alignment
    max_idx_len = len(str(len(display_files)))
    max_name_len = max(len(f.name) for f in display_files)
    
    for i, f in enumerate(display_files):
        is_default = (i == default_index) or (f.name == default_selection)
        
        # Style logic
        idx_str = f"[{i+1}]".rjust(max_idx_len + 2)
        color = bcolors.OKGREEN if is_default else bcolors.OKBLUE
        marker = " (Default)" if is_default else ""
        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
        
        # Beautiful formatted line
        print(f"{bcolors.GREY}[{mtime}]{bcolors.ENDC} {color}{idx_str} {f.name.ljust(max_name_len)}{marker}{bcolors.ENDC}")

    while True:
        prompt_text = f"Enter number (1-{len(display_files)})"
        if default_index != -1:
            prompt_text += f" [Default: {default_index + 1}]"
        
        choice_str = print_input_prompt(prompt_text)
        
        if not choice_str and default_index != -1:
             # Handle default selection logic similar to original...
             if default_index < len(files):
                 selected = files[default_index]
                 print_success(f"Selected Default: {selected.name}")
                 return selected
        
        if not choice_str:
             print_warning("Selection cancelled.")
             return None

        try:
            choice_idx = int(choice_str) - 1
            if 0 <= choice_idx < len(display_files):
                selected = display_files[choice_idx]
                print_success(f"Selected: {selected.name}")
                return selected
            else:
                print_warning("Invalid choice.")
        except ValueError:
            print_warning("Please enter a number.")
            
# --- FOLDER SELECTION UI (Simplified for brevity, use same pattern as above) ---
def select_folder(base_dir: Path, num_to_show: int = 9) -> Path | None:
    # ... (You can apply the same styling logic here) ...
    # For now, just ensuring the import works for your audit_runner
    base_dir.mkdir(exist_ok=True)
    folders = sorted([f for f in base_dir.iterdir() if f.is_dir()], key=lambda f: f.stat().st_mtime, reverse=True)[:num_to_show]
    
    if not folders: return None
    
    print_header("SELECT RUN FOLDER")
    for i, f in enumerate(folders):
        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
        print(f"{bcolors.GREY}[{mtime}]{bcolors.ENDC} {bcolors.OKBLUE}[{i+1}] {f.name}{bcolors.ENDC}")
        
    choice_str = print_input_prompt(f"Enter number (1-{len(folders)})")
    try:
        idx = int(choice_str) - 1
        if 0 <= idx < len(folders): return folders[idx]
    except: pass
    return None