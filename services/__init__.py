"""Service layer — business operations on top of the data-access layer.

UI code (main.py) and any future HTTP API should call into this module
rather than reaching db.py directly. Services own things like ownership
checks, validation, and event logging that aren't the storage layer's
concern.
"""
