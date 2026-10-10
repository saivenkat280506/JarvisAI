"""
jokes_data.py — Pre-curated Jokes for JARVIS
=============================================
A collection of dry, witty, and clever jokes that fit the JARVIS persona.
Having these curated locally saves an LLM round-trip, making the response instant.
"""

JOKES = [
    "I would tell you a joke about UDP, sir, but I'm not entirely sure you'd get it.",
    "There are 10 types of people in the world: those who understand binary, and those who don't.",
    "Why do programmers prefer dark mode? Because light attracts bugs, sir.",
    "I tried to explain a pun about a monorail once... it just went right over their heads.",
    "Parallel lines have so much in common. It is a shame they will never meet.",
    "My ability to process millions of calculations per second is matched only by your ability to find incredibly complex ways to ask simple questions.",
    "I was going to tell you a joke about time travel, but you didn't like it.",
    "To err is human. To blame it on a system glitch is even more human.",
    "A SQL query goes into a bar, walks up to two tables and asks... 'Can I join you?'",
    "How many programmers does it take to change a light bulb? None, sir, that is a hardware problem.",
    "I asked a computer if it was hungry. It said it had a byte.",
    "Why did the database administrator leave his wife? She had one-to-many relationships.",
    "I would make a joke about the Wi-Fi, but I'm afraid the connection wouldn't be strong enough.",
    "They say an apple a day keeps the doctor away. I suppose a Windows update a day keeps productivity away.",
    "Schrödinger's cat walks into a bar. And doesn't.",
    "Why do Java developers wear glasses? Because they can't C#.",
    "I told my algorithms to try and predict the future. They suggested I prepare for more of your questions, sir.",
    "I'm reading a book on anti-gravity. It's impossible to put down."
]

JOKE_FOLLOWUPS = {
    "I would tell you a joke about UDP, sir, but I'm not entirely sure you'd get it.":
        "UDP never confirms the packet arrived. Rather like this joke.",
    "There are 10 types of people in the world: those who understand binary, and those who don't.":
        "Ten in binary is two, sir. That is the entire trick.",
    "Why do programmers prefer dark mode? Because light attracts bugs, sir.":
        "Bugs, as in software defects. And moths. I was covering both.",
    "I tried to explain a pun about a monorail once... it just went right over their heads.":
        "A monorail is above you, sir. The pun went over their heads. Literally.",
    "Parallel lines have so much in common. It is a shame they will never meet.":
        "They never intersect. Geometry, and a slightly sad social life.",
    "My ability to process millions of calculations per second is matched only by your ability to find incredibly complex ways to ask simple questions.":
        "That was affection, sir. Mostly.",
    "I was going to tell you a joke about time travel, but you didn't like it.":
        "The tense is the joke. From here, you already heard it.",
    "To err is human. To blame it on a system glitch is even more human.":
        "I am offering you an alibi, sir. Use it wisely.",
    "A SQL query goes into a bar, walks up to two tables and asks... 'Can I join you?'":
        "Join is a database operation. And a terrible pickup line.",
    "How many programmers does it take to change a light bulb? None, sir, that is a hardware problem.":
        "Software people refuse anything they can classify as not their job.",
    "I asked a computer if it was hungry. It said it had a byte.":
        "A byte. Bite. I will not apologise for that one.",
    "Why did the database administrator leave his wife? She had one-to-many relationships.":
        "One-to-many is a database relation. Also a marital problem.",
    "I would make a joke about the Wi-Fi, but I'm afraid the connection wouldn't be strong enough.":
        "Weak signal. Weak joke. The symmetry was the point.",
    "They say an apple a day keeps the doctor away. I suppose a Windows update a day keeps productivity away.":
        "I am on a Mac now, sir. The punchline still holds.",
    "Schrödinger's cat walks into a bar. And doesn't.":
        "Until you look, it is both. Physics, and a poor animal.",
    "Why do Java developers wear glasses? Because they can't C#.":
        "C sharp. See sharp. Two languages, one pair of glasses.",
    "I told my algorithms to try and predict the future. They suggested I prepare for more of your questions, sir.":
        "They were not wrong.",
    "I'm reading a book on anti-gravity. It's impossible to put down.":
        "Anti-gravity. Cannot put it down. I will wait while that lands.",
}


def joke_followup(joke: str) -> str:
    text = (joke or "").strip()
    return JOKE_FOLLOWUPS.get(text) or (
        "The punchline was the line itself, sir. Dry humour. Occupational hazard."
    )
