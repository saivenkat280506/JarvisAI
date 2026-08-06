"""
open_app.py — Application Launcher
==================================
Handles launching system applications using protocols, shell commands, and fallbacks.
"""

import subprocess
import webbrowser
import os
import shutil
import json

def get_app_path(app_name: str) -> str:
    """Helper to resolve the path of an application using registry, start menu, or PATH."""
    name_lower = app_name.lower().strip()
    
    # 1. Alias check — prefer classic System32 tools over Store/WindowsApps stubs
    aliases = {
        "calculator": "calc.exe",
        "calc": "calc.exe",
        "paint": "mspaint.exe",
        "cmd": "cmd.exe",
        "command prompt": "cmd.exe",
        "terminal": "wt.exe",
        "powershell": "powershell.exe",
        "task manager": "taskmgr.exe",
        "taskmanager": "taskmgr.exe",
        "settings": "ms-settings:",
        "control panel": "control",
        "file explorer": "explorer.exe",
        "explorer": "explorer.exe",
        "notepad": "notepad.exe",
        "notepads": "notepad.exe",
    }
    if name_lower in aliases:
        name_lower = aliases[name_lower]
        
    if name_lower.endswith(":"):
        return name_lower

    # Prefer PATH / System32 for well-known system binaries (avoids broken WindowsApps paths)
    system_bins = {
        "notepad.exe", "calc.exe", "mspaint.exe", "cmd.exe", "powershell.exe",
        "taskmgr.exe", "explorer.exe", "wt.exe", "control.exe",
    }
    if name_lower in system_bins or name_lower.rstrip(".exe") + ".exe" in system_bins:
        which_name = name_lower if name_lower.endswith(".exe") else f"{name_lower}.exe"
        exe_path = shutil.which(which_name) or shutil.which(name_lower)
        if exe_path and "windowsapps" not in exe_path.lower():
            return exe_path
        system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", which_name)
        if os.path.exists(system32):
            return system32
        
    # 2. Registry search
    registry = {}
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        registry_path = os.path.join(current_dir, "apps_registry.json")
        if os.path.exists(registry_path):
            with open(registry_path, "r", encoding="utf-8") as f:
                registry = json.load(f)
    except Exception:
        pass
        
    if name_lower in registry:
        return registry[name_lower]
        
    for key, val in registry.items():
        if name_lower in key or key in name_lower:
            return val
            
    # 3. Start Menu crawling (skip WindowsApps packages that cannot be launched)
    user_programs = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs")
    common_programs = os.path.join(os.environ.get("ALLUSERSPROFILE", "C:\\ProgramData"), "Microsoft", "Windows", "Start Menu", "Programs")
    search_dirs = [user_programs, common_programs]
    for search_dir in search_dirs:
        if os.path.exists(search_dir):
            for root, dirs, files in os.walk(search_dir):
                for file in files:
                    if file.lower().endswith(".lnk"):
                        link_name = os.path.splitext(file)[0].lower()
                        if name_lower in link_name or link_name in name_lower:
                            full = os.path.join(root, file)
                            if "windowsapps" not in full.lower():
                                return full
                            
    # 4. Path check using shutil.which
    exe_path = shutil.which(name_lower)
    if exe_path:
        return exe_path
        
    return name_lower

def open_app(app_name: str):
    """
    Opens a specified application based on Windows-specific logic.
    
    Args:
        app_name (str): Name of the application to launch.
        
    Returns:
        tuple: (success: bool, message: str)
    """
    name_lower = app_name.lower().strip()
    
    # 1. WhatsApp: Use local executable check and protocol fallback
    if "whatsapp" in name_lower:
        try:
            from executor.automation import open_whatsapp
            return open_whatsapp()
        except ImportError:
            try:
                from automation import open_whatsapp
                return open_whatsapp()
            except ImportError:
                user_home = os.path.expanduser("~")
                whatsapp_path = os.path.join(user_home, "AppData", "Local", "WhatsApp", "WhatsApp.exe")
                if os.path.exists(whatsapp_path):
                    try:
                        subprocess.Popen([whatsapp_path], shell=False)
                        return True, "Successfully opened WhatsApp Desktop."
                    except Exception as e:
                        pass
                try:
                    subprocess.run("start whatsapp:", shell=True, check=True)
                    return True, "Successfully opened WhatsApp using protocol."
                except Exception as e:
                    return False, f"failed to open {app_name}: {str(e)}"

    # 2. Browser (Arc with Fallback)
    if name_lower == "browser" or name_lower == "arc":
        try:
            # Try to launch arc.exe directly (assuming it's in PATH or accessible via shell)
            subprocess.Popen("arc.exe", shell=True)
            return True, "Successfully opened Arc browser."
        except Exception:
            try:
                # Fallback: Open the default system browser
                webbrowser.open("about:blank")
                return True, "Arc browser not found. Opened default system browser as fallback."
            except Exception as e:
                return False, f"failed to open {app_name}: {str(e)}"

    # 3. Resolve app path
    path = get_app_path(app_name)
    if path.endswith(":"):
        try:
            os.startfile(path)
            return True, f"Successfully opened {app_name}."
        except Exception as e:
            return False, f"failed to open {app_name}: {str(e)}"
            
    if os.path.exists(path):
        try:
            os.startfile(path)
            return True, f"Successfully opened {app_name}."
        except Exception as e:
            try:
                subprocess.Popen(f'start "" "{path}"', shell=True)
                return True, f"Successfully opened {app_name}."
            except Exception as ex:
                return False, f"failed to open {app_name}: {str(ex)}"
                
    # Fallback to system path execution
    try:
        exe_path = shutil.which(path)
        if exe_path:
            subprocess.Popen(exe_path, shell=False)
            return True, f"Successfully opened {app_name}."
    except Exception:
        pass
        
    return False, f"failed to open {app_name}. Please make sure it is installed."

if __name__ == "__main__":
    # Test cases
    test_apps = ["whatsapp", "browser", "notepad", "calc"]
    
    for app in test_apps:
        print(f"Testing {app}...")
        success, msg = open_app(app)
        print(f"Result: {success} {msg}\n")
