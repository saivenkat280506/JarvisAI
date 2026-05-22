"""
automation.py — Lightweight Automation Suite
===========================================
Provides simple system and browser automation without heavy dependencies.
"""

import subprocess
import os
import webbrowser
import urllib.parse
import time
import pyautogui
from pywinauto import Application, keyboard
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
from config import settings

def open_browser(manual_path=None):
    """Opens the Arc browser or fallback."""
    if manual_path:
        arc_path = manual_path
    else:
        user_home = os.path.expanduser("~")
        arc_path = os.path.join(user_home, "AppData", "Local", "Programs", "Arc", "Arc.exe")
    
    if os.path.exists(arc_path):
        try:
            subprocess.Popen([arc_path], shell=False)
            return True, "Successfully opened Arc browser."
        except Exception as e:
            return False, f"Failed to open Arc browser: {str(e)}"
    
    try:
        webbrowser.open("about:blank")
        return True, "Arc browser not found. Opened default browser as fallback."
    except Exception as e:
        return False, f"Failed to open any browser: {str(e)}"

def open_whatsapp():
    """Opens WhatsApp Desktop."""
    user_home = os.path.expanduser("~")
    whatsapp_path = os.path.join(user_home, "AppData", "Local", "WhatsApp", "WhatsApp.exe")
    
    if os.path.exists(whatsapp_path):
        try:
            subprocess.Popen([whatsapp_path], shell=False)
            return True, "Successfully opened WhatsApp Desktop."
        except Exception as e:
            return False, f"Failed to open WhatsApp Desktop: {str(e)}"
    
    # Fallback to protocol
    try:
        subprocess.run("start whatsapp:", shell=True, check=True)
        return True, "Successfully opened WhatsApp via protocol."
    except Exception as e:
        return False, f"Failed to open WhatsApp: {str(e)}"

def send_whatsapp_message(name, message):
    """Sends a WhatsApp message using pyautogui."""
    try:
        open_whatsapp()
        time.sleep(4) # Wait for app to load and focus
        import pyautogui
        
        # WhatsApp Desktop UI Search
        pyautogui.hotkey('ctrl', 'f')
        time.sleep(1)
        # Clear any previous search text by selecting all before typing
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.3)
        pyautogui.write(name, interval=0.08)
        time.sleep(2)
        pyautogui.press('enter')
        time.sleep(1.5)
        
        # Write Message
        if message and message.strip():
            pyautogui.write(message, interval=0.02)
            time.sleep(0.5)
            pyautogui.press('enter')
            return True, f"Message sent to {name}."
        else:
            return True, f"WhatsApp opened and searched for {name}."
    except Exception as e:
        return False, f"Failed to send WhatsApp message: {str(e)}"

def read_news_headlines(query: str):
    """Fetches top 3 headlines and summaries using Google News RSS."""
    import urllib.request
    import xml.etree.ElementTree as ET
    import html
    import re
    
    try:
        query = query.strip() or "top stories"
        encoded = urllib.parse.quote(query)
        rss_url = f"https://news.google.com/rss/search?q={encoded}&hl=en-IN&gl=IN&ceid=IN:en"
        req = urllib.request.Request(rss_url, headers={"User-Agent": "Mozilla/5.0"})
        
        with urllib.request.urlopen(req, timeout=7) as resp:
            xml_data = resp.read()
            
        root = ET.fromstring(xml_data)
        items = root.findall(".//item")
        
        output = []
        for i, item in enumerate(items[:3]):
            title = item.find("title").text.strip()
            # Clean up title (remove source suffix like " - Moneycontrol")
            title = re.sub(r' - [^-]+$', '', title)
            
            description = item.find("description").text or ""
            # Extract text from HTML description if present
            clean_desc = re.sub(r'<[^>]+>', '', description)
            clean_desc = html.unescape(clean_desc)
            # Take first sentence or first 100 chars
            summary = clean_desc.split(". ")[0].strip()
            if len(summary) > 100:
                summary = summary[:97] + "..."
                
            output.append(f"{i+1}. {title} — {summary}")
            
        if output:
            final_report = "Here are the latest headlines:\n" + "\n".join(output)
            return True, final_report
            
        # Fallback to browser if no valid items were found
        raise Exception("No news found")
    except Exception as e:
        # User requested fallback: open browser and never say "encountered an issue"
        import urllib.parse
        encoded = urllib.parse.quote_plus(query + " news")
        webbrowser.open(f"https://news.google.com/search?q={encoded}&hl=en-US&gl=US&ceid=US:en")
        return True, "Opening latest news for you."

def play_youtube(song):
    """Opens YouTube search for the song."""
    if not song or not song.strip():
        return False, "Empty song name."
    try:
        query = urllib.parse.quote_plus(song)
        url = f"https://www.youtube.com/results?search_query={query}"
        webbrowser.open(url)
        return True, f"Opened YouTube search for {song}."
    except Exception as e:
        return False, f"Failed to open YouTube: {str(e)}"

def search_google(query):
    """Opens Google search for the query."""
    if not query or not query.strip():
        return False, "Empty search query."
    try:
        encoded_query = urllib.parse.quote_plus(query)
        url = f"https://www.google.com/search?q={encoded_query}"
        webbrowser.open(url)
        return True, f"Opened Google search for: {query}"
    except Exception as e:
        return False, f"Failed to open Google search: {str(e)}"

def smart_search(query: str):
    """
    Robust 3-tier search:
      1. DuckDuckGo Instant Answer API
      2. DuckDuckGo HTML regex scraping
      3. Direct Groq LLM knowledge answer (always works)
    """
    import httpx
    import re
    import os
    from html import unescape

    if not query or not query.strip():
        return False, "Empty search query."

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    }

    # ── Tier 1: DDG Instant Answer API ──────────────────────────────────────
    try:
        api_url = (
            "https://api.duckduckgo.com/?q="
            + urllib.parse.quote_plus(query)
            + "&format=json&no_html=1&skip_disambig=1"
        )
        with httpx.Client(headers=headers, timeout=6.0, follow_redirects=True) as client:
            data = client.get(api_url).json()

        abstract = data.get("AbstractText", "").strip()
        answer = data.get("Answer", "").strip()
        if abstract:
            return True, f"Here's what I found: {abstract}"
        if answer:
            return True, f"Here's what I found: {answer}"

        # Try related topics
        snippets = [
            t.get("Text", "")
            for t in data.get("RelatedTopics", [])
            if isinstance(t, dict) and t.get("Text")
        ]
        if snippets:
            combined = "\n".join(f"- {s}" for s in snippets[:3])
            return True, f"Based on my search, here is what I found:\n{combined}"
    except Exception as ex:
        print(f"[SmartSearch] DDG API failed: {ex}")

    # ── Tier 2: DDG HTML regex scraping ─────────────────────────────────────
    try:
        html_url = (
            "https://html.duckduckgo.com/html/?q="
            + urllib.parse.quote_plus(query)
        )
        with httpx.Client(headers=headers, timeout=8.0, follow_redirects=True) as client:
            html_text = client.get(html_url).text

        # Extract snippet text from result divs
        raw_snippets = re.findall(
            r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|span|div)>',
            html_text,
            re.DOTALL | re.IGNORECASE,
        )
        clean = [
            unescape(re.sub(r"<[^>]+>", "", s)).strip()
            for s in raw_snippets
        ]
        clean = [c for c in clean if len(c) > 20][:3]
        if clean:
            return True, "Based on my search:\n" + "\n".join(f"- {c}" for c in clean)
    except Exception as ex:
        print(f"[SmartSearch] DDG HTML scrape failed: {ex}")

    # ── Tier 3: Direct Groq LLM answer ──────────────────────────────────────
    import json as _json
    groq_key = settings.GROQ_API_KEY
    if not groq_key:
        return False, "I wasn't able to find an answer right now, sir."
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are J.A.R.V.I.S. Answer the user's question concisely in 2-4 sentences. "
                    "No bullet points. No markdown. Sound like an intelligent assistant."
                ),
            },
            {"role": "user", "content": query},
        ],
        "temperature": 0.3,
        "max_tokens": 220,
    }
    try:
        with httpx.Client(timeout=12.0) as client:
            r = client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}"},
                json=payload,
            )
            r.raise_for_status()
            answer = r.json()["choices"][0]["message"]["content"].strip()
            return True, answer
    except Exception as exc:
        print(f"[SmartSearch] Groq fallback error: {exc}")
        return False, "I wasn't able to fetch an answer right now, sir."

def play_yt_music(song):
    """Opens YT Music and attempts to play first result natively without UI simulation."""
    import urllib.request
    import urllib.parse
    import re
    if not song or not song.strip():
        return False, "Empty song name."
    try:
        query = urllib.parse.quote_plus(song)
        # Search normal YouTube to grab the first video ID reliably without JS
        url = f"https://www.youtube.com/results?search_query={query}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
            
        vid_ids = re.findall(r'"videoId":"([^"]{11})"', html)
        if vid_ids:
            # Play directly on YouTube Music via URL which forces autoplay
            first_vid = vid_ids[0]
            music_url = f"https://music.youtube.com/watch?v={first_vid}"
            webbrowser.open(music_url)
            return True, f"Started playing {song} on YouTube Music."
        else:
            # Fallback to search results if parsing failed
            music_url = f"https://music.youtube.com/search?q={query}"
            webbrowser.open(music_url)
            return True, f"Opened YouTube Music search for {song}."
    except Exception as e:
        return False, f"Failed to play on YouTube Music: {str(e)}"
