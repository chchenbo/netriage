"""The startup wordmark.

figlet's small font, uppercase. Lowercase at this height merges into
unreadable strokes, and the slanted fonts glue letters together with
long underscore runs; uppercase small stays legible at four rows.
"""

# 4 rows, 38 columns
LOGO_LINES = [
    ' _  _ ___ _____ ___ ___   _   ___ ___',
    '| \\| | __|_   _| _ \\_ _| /_\\ / __| __|',
    '| .` | _|  | | |   /| | / _ \\ (_ | _|',
    '|_|\\_|___| |_| |_|_\\___/_/ \\_\\___|___|',
]

LOGO = "\n".join(LOGO_LINES)
