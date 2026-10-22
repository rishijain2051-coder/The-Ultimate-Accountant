"""The Keeper: Tally's books, read and kept, encrypted, in the user's own Drive.

Read-only all the way through. Nothing in this package may import the Tally
Bridge (app.bridge), which writes to Tally, or Assurance Console
(app.backend); the only piece it shares with them is app.tally_line. See
PLAN-keeper.md, section 2, and tests/test_kept_apart.py, which holds it.
"""
