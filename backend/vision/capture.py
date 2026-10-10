"""MCP-backed screenshot capture with optional lightweight edge overlays."""

from __future__ import annotations

import base64
import io

from PIL import Image

from mcp_clients import macos as mcp_gui


def capture_screen_base64(draw_boxes: bool = True) -> str:
    """Capture through the macOS MCP server and return a base64 PNG image."""
    size = mcp_gui.gui_screen_size()
    width = max(1, min(int(size.get("width", 0)), 8192))
    height = max(1, min(int(size.get("height", 0)), 8192))
    if width < 2 or height < 2:
        raise RuntimeError("Could not read screen dimensions through macOS MCP.")

    payload = mcp_gui.gui_screenshot_region(0, 0, width, height)
    image = Image.open(io.BytesIO(base64.b64decode(payload))).convert("RGB")
    image.thumbnail((1280, 720))

    if draw_boxes:
        try:
            import cv2
            import numpy as np

            pixels = np.array(image)
            edges = cv2.Canny(cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY), 50, 150)
            contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                if 20 < w < 400 and 20 < h < 100:
                    cv2.rectangle(pixels, (x, y), (x + w, y + h), (0, 0, 255), 2)
            image = Image.fromarray(pixels)
        except ImportError:
            pass

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")
