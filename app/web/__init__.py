"""C3P0 web dashboard.

Runs as a separate process/container from the bot (`python -m app.web`
instead of `python -m app`), sharing the same SQLite database and reusing
the bot's service/repository layer directly - see app/services/*.py's
module docstrings for why those are built to be callable from here.
"""
