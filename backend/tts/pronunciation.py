"""
pronunciation.py — TTS pronunciation overrides
================================================
Maps words Pocket TTS mispronounces to phonetic spellings the model reads correctly.
"""

import re

# Longer phrases first when applying replacements.
PRONUNCIATION_MAP: dict[str, str] = {
    # Indian cuisine (common TTS misreads)
    "chole bhature": "cholay bahtooray",
    "chole bhatura": "cholay bahtooray",
    "chole bature": "cholay bahtooray",
    "bhature": "bahtooray",
    "bhatura": "bahtooray",
    "bature": "bahtooray",
    "chole": "cholay",
    "chana": "chunna",
    "masala": "mahsala",
    "biryani": "beer-yah-nee",
    "paneer": "pah-neer",
    "tikka": "tikah",
    "naan": "nahn",
    "roti": "roh-tee",
    "paratha": "prah-tah",
    "samosa": "sah-moh-sah",
    "pakora": "pah-korah",
    "rajma": "rahj-mah",
    "dosa": "doh-sah",
    "idli": "id-lee",
    "vada": "vah-dah",
    "pav": "pahv",
    "bhaji": "bah-jee",
    "dal": "dahl",
    "daal": "dahl",
    "ghee": "ghee",
    "chai": "chy",
    "lassi": "lah-see",
    "kulcha": "kool-chah",
    "keema": "kee-mah",
    "korma": "kor-mah",
    "saag": "sahg",
    "palak": "pah-luck",
    "aloo": "ah-loo",
    "gobi": "goh-bee",
    "matar": "mah-tar",
    "chutney": "chut-nee",
    "raita": "ry-tah",
    "papad": "pah-pud",
    "puri": "poo-ree",
    "kachori": "kah-chor-ee",
    "jalebi": "jah-lay-bee",
    "gulab": "goo-lub",
    "jamun": "jah-mun",
    "halwa": "hul-wah",
    "kheer": "keer",
    "tandoori": "tan-door-ee",
    "seekh": "seek",
    "kebab": "keh-bob",
    "hummus": "hoo-mus",
    "tzatziki": "zaht-zee-kee",
    "quinoa": "keen-wah",
    "gyro": "yee-roh",
    "pho": "fuh",
    "sriracha": "see-rah-chah",
    "chipotle": "chi-poht-lay",
    "cilantro": "sih-lan-tro",
    "jalapeno": "hah-lah-pay-nyo",
    "jalapeño": "hah-lah-pay-nyo",
    "aioli": "ay-oh-lee",
    "niche": "neesh",
    "segue": "seg-way",
    "cache": "cash",
    "router": "row-ter",
    "nginx": "engine-x",
    "kubectl": "kube-control",
    "linux": "linn-ux",
    "ubuntu": "oo-boon-too",
    "debian": "deb-ee-an",
    "archlinux": "ark linux",
}

_SORTED_ENTRIES = sorted(PRONUNCIATION_MAP.items(), key=lambda item: len(item[0]), reverse=True)
_COMPILED_PATTERNS = [
    (re.compile(rf"(?<!\w){re.escape(source)}(?!\w)", re.IGNORECASE), spoken)
    for source, spoken in _SORTED_ENTRIES
]


def apply_pronunciation_fixes(text: str) -> str:
    """Rewrite known mispronounced tokens to phonetic spellings for TTS."""
    if not text:
        return text

    for pattern, spoken in _COMPILED_PATTERNS:
        text = pattern.sub(spoken, text)
    return text