"""
open_app.py — Application Launcher
==================================
Handles launching system applications using protocols, shell commands, and fallbacks.
"""

import subprocess
import webbrowser
import os

def open_app(app_name: str):
    """
    Opens a specified application based on Windows-specific logic.
    
    Args:
        app_name (str): Name of the application to launch.
        
    Returns:
        tuple: (success: bool, message: str)
    """
    name_lower = app_name.lower()
    
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
                    return False, f"Error launching WhatsApp: {str(e)}"

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
                return False, f"Failed to open any browser: {str(e)}"

    # 3. Generic App Opening
    try:
        # Attempt to launch using the system shell (similar to CMD 'start')
        # This works if the app is registered in the system PATH
        subprocess.Popen(f"start {app_name}", shell=True)
        return True, f"Successfully attempted to launch {app_name} via shell."
    except Exception as e:
        return False, f"Could not find or launch {app_name}: {str(e)}"

if __name__ == "__main__":
    # Test cases
    test_apps = ["whatsapp", "browser", "notepad", "calc"]
    
    for app in test_apps:
        print(f"Testing {app}...")
        success, msg = open_app(app)
        print(f"Result: {'✅' if success else '❌'} {msg}\n")
